package repositories

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strings"

	"github.com/edaslakavicius/emerging-app-finder/backend/db"
	"github.com/edaslakavicius/emerging-app-finder/backend/helpers"
	"gorm.io/gorm"
)

// GatherExplainerInput reads the grounding material for one advertiser's
// explainer inside a READ ONLY transaction: the app name/genre/store
// description (the most recent app the advertiser ran ads for), up to 10
// scraped creative title+body pairs (active ads preferred), the ad CTA, and the
// funnel landing URL. It returns the assembled input plus a stable
// source_fingerprint (sha256 of the canonicalized inputs) used to skip
// regeneration when nothing the model sees has changed.
func GatherExplainerInput(ctx context.Context, g *gorm.DB, advertiserID int) (helpers.ExplainerInput, string, error) {
	var in helpers.ExplainerInput
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		// Most recent app for this advertiser (mirrors the matview's app pick:
		// last_seen DESC). description is the public.apps column added by Track A.
		var app struct {
			Name        *string `gorm:"column:name"`
			Genre       *string `gorm:"column:genre"`
			Description *string `gorm:"column:description"`
		}
		if err := tx.Raw(
			`SELECT ap.name, ap.genre, ap.description
			 FROM public.ads a
			 JOIN public.apps ap ON ap.id = a.app_id
			 WHERE a.advertiser_id = ? AND a.app_id IS NOT NULL
			 ORDER BY a.last_seen DESC NULLS LAST
			 LIMIT 1`,
			advertiserID,
		).Scan(&app).Error; err != nil {
			return err
		}
		in.AppName = derefStr(app.Name)
		in.Genre = derefStr(app.Genre)
		in.AppDescription = derefStr(app.Description)

		// Up to 10 creatives, active ads first, then most recent.
		var creatives []struct {
			Title    *string `gorm:"column:title"`
			BodyText *string `gorm:"column:body_text"`
		}
		if err := tx.Raw(
			`SELECT c.title, c.body_text
			 FROM public.creatives c
			 JOIN public.ads a ON a.id = c.ad_id
			 WHERE a.advertiser_id = ?
			   AND (c.title IS NOT NULL OR c.body_text IS NOT NULL)
			 ORDER BY a.is_active DESC NULLS LAST, a.last_seen DESC NULLS LAST, c.id DESC
			 LIMIT 10`,
			advertiserID,
		).Scan(&creatives).Error; err != nil {
			return err
		}
		for _, c := range creatives {
			in.Creatives = append(in.Creatives, helpers.ExplainerCreative{
				Title:    derefStr(c.Title),
				BodyText: derefStr(c.BodyText),
			})
		}

		// CTA from the most recent ad that has one.
		var cta *string
		if err := tx.Raw(
			`SELECT a.cta_text
			 FROM public.ads a
			 WHERE a.advertiser_id = ? AND a.cta_text IS NOT NULL AND a.cta_text <> ''
			 ORDER BY a.last_seen DESC NULLS LAST
			 LIMIT 1`,
			advertiserID,
		).Scan(&cta).Error; err != nil {
			return err
		}
		in.CTA = derefStr(cta)

		// Funnel landing URL (one capture per ad; take the most recent).
		var landing *string
		if err := tx.Raw(
			`SELECT f.landing_url
			 FROM public.funnels f
			 JOIN public.ads a ON a.id = f.ad_id
			 WHERE a.advertiser_id = ? AND f.landing_url IS NOT NULL AND f.landing_url <> ''
			 ORDER BY f.captured_at DESC NULLS LAST
			 LIMIT 1`,
			advertiserID,
		).Scan(&landing).Error; err != nil {
			return err
		}
		in.LandingURL = derefStr(landing)
		return nil
	})
	if err != nil {
		return helpers.ExplainerInput{}, "", err
	}
	return in, fingerprintExplainerInput(in), nil
}

// fingerprintExplainerInput derives a stable sha256 over the canonicalized
// model inputs. The same inputs always yield the same digest, so an unchanged
// advertiser is skipped on re-runs (cheap idempotence vs. the metered model).
func fingerprintExplainerInput(in helpers.ExplainerInput) string {
	var b strings.Builder
	b.WriteString(in.AppName)
	b.WriteByte('\n')
	b.WriteString(in.Genre)
	b.WriteByte('\n')
	b.WriteString(in.AppDescription)
	b.WriteByte('\n')
	b.WriteString(in.CTA)
	b.WriteByte('\n')
	b.WriteString(in.LandingURL)
	b.WriteByte('\n')
	for _, c := range in.Creatives {
		b.WriteString(c.Title)
		b.WriteByte('\x1f') // unit separator: unambiguous title/body boundary
		b.WriteString(c.BodyText)
		b.WriteByte('\n')
	}
	sum := sha256.Sum256([]byte(b.String()))
	return hex.EncodeToString(sum[:])
}

// GetExplainerFingerprint returns the stored source_fingerprint for an
// advertiser, or nil when no explainer row exists yet.
func GetExplainerFingerprint(ctx context.Context, g *gorm.DB, advertiserID int) (*string, error) {
	var fp *string
	err := db.WithReadTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Raw(
			`SELECT source_fingerprint FROM adtech.advertiser_explainers WHERE advertiser_id = ?`,
			advertiserID,
		).Scan(&fp).Error
	})
	return fp, err
}

// UpsertExplainer writes (or refreshes) an advertiser's explainer row inside a
// write transaction. On conflict it overwrites the three fields, the model, the
// fingerprint, and stamps generated_at — mirroring UpsertSavedGem's pattern.
func UpsertExplainer(ctx context.Context, g *gorm.DB, advertiserID int, ex helpers.Explainer, model, fingerprint string) error {
	return db.WithWriteTx(ctx, g, func(tx *gorm.DB) error {
		return tx.Exec(
			`INSERT INTO adtech.advertiser_explainers
			   (advertiser_id, what_it_does, problem_solved, why_working,
			    model, source_fingerprint, generated_at)
			 VALUES (?, ?, ?, ?, ?, ?, clock_timestamp())
			 ON CONFLICT (advertiser_id) DO UPDATE SET
			   what_it_does       = EXCLUDED.what_it_does,
			   problem_solved     = EXCLUDED.problem_solved,
			   why_working        = EXCLUDED.why_working,
			   model              = EXCLUDED.model,
			   source_fingerprint = EXCLUDED.source_fingerprint,
			   generated_at       = clock_timestamp()`,
			advertiserID, ex.WhatItDoes, ex.ProblemSolved, ex.WhyWorking,
			model, fingerprint,
		).Error
	})
}

// GenerateAndStore is the end-to-end unit for one advertiser: gather grounding
// material, skip when the fingerprint already matches the stored row (unless
// forced), otherwise call Gemini and upsert. It returns whether a row was
// written (false = skipped/unchanged) so the runner can log skip counts.
func GenerateAndStore(ctx context.Context, g *gorm.DB, gemini *helpers.GeminiHelper, advertiserID int, force bool) (bool, error) {
	if gemini == nil {
		return false, fmt.Errorf("gemini helper not initialized")
	}
	in, fingerprint, err := GatherExplainerInput(ctx, g, advertiserID)
	if err != nil {
		return false, fmt.Errorf("gather advertiser %d: %w", advertiserID, err)
	}
	if !force {
		stored, err := GetExplainerFingerprint(ctx, g, advertiserID)
		if err != nil {
			return false, fmt.Errorf("fingerprint advertiser %d: %w", advertiserID, err)
		}
		if stored != nil && *stored == fingerprint {
			return false, nil // unchanged → skip the model call
		}
	}
	ex, err := gemini.GenerateExplainer(ctx, in)
	if err != nil {
		return false, fmt.Errorf("generate advertiser %d: %w", advertiserID, err)
	}
	if err := UpsertExplainer(ctx, g, advertiserID, ex, "gemini-2.0-flash", fingerprint); err != nil {
		return false, fmt.Errorf("upsert advertiser %d: %w", advertiserID, err)
	}
	return true, nil
}

func derefStr(p *string) string {
	if p == nil {
		return ""
	}
	return *p
}
