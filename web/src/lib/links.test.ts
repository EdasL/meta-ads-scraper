/**
 * Pure-function tests for lib/links.ts. Zero-dependency: run with
 *   node --test src/lib/links.test.ts
 * Node 22 strips the TS types at load; the only `@/` import in links.ts is
 * type-only (stripped), so the relative `.ts` import below resolves cleanly.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildGemLinks, bareDomain, externalHref, adLibraryAdUrl, adLibraryPageUrl } from "./links.ts";

/** Minimal dossier stub — buildGemLinks only reads company + apps. */
function gem(overrides) {
  return {
    company: { page_id: undefined, domain: undefined },
    apps: [],
    ...overrides,
  };
}

test("bareDomain strips scheme + trailing slashes", () => {
  assert.equal(bareDomain("https://sleepwave.app/"), "sleepwave.app");
  assert.equal(bareDomain("http://x.com"), "x.com");
  assert.equal(bareDomain("sleepwave.app"), "sleepwave.app");
  assert.equal(bareDomain(null), "");
  assert.equal(bareDomain(undefined), "");
});

test("externalHref keeps a scheme, adds https when missing", () => {
  assert.equal(externalHref("https://sleepwave.app/start"), "https://sleepwave.app/start");
  assert.equal(externalHref("http://x.com"), "http://x.com");
  assert.equal(externalHref("sleepwave.app/start"), "https://sleepwave.app/start");
});

test("ad library urls pin the country filter to US", () => {
  assert.equal(
    adLibraryAdUrl("998877"),
    "https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=US&id=998877",
  );
  assert.match(adLibraryPageUrl("12345"), /[?&]country=US(&|$)/);
  assert.match(adLibraryPageUrl("12345", true), /[?&]country=US(&|$)/);
});

test("buildGemLinks: empty when no page_id / domain / app", () => {
  assert.deepEqual(buildGemLinks(gem({})), []);
});

test("buildGemLinks: page_id yields profile + ads library", () => {
  const links = buildGemLinks(gem({ company: { page_id: "12345", domain: undefined } }));
  assert.equal(links.length, 2);
  assert.equal(links[0].label, "Facebook page");
  assert.equal(links[0].href, "https://www.facebook.com/12345");
  assert.equal(links[1].label, "Meta Ad Library");
  assert.equal(
    links[1].href,
    "https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=US&view_all_page_id=12345",
  );
});

test("buildGemLinks: domain normalized to https website", () => {
  const links = buildGemLinks(gem({ company: { page_id: undefined, domain: "https://sleepwave.app/" } }));
  assert.equal(links.length, 1);
  assert.equal(links[0].label, "Website");
  assert.equal(links[0].href, "https://sleepwave.app");
});

test("buildGemLinks: ios store_id already prefixed with id is used as-is", () => {
  const links = buildGemLinks(gem({ apps: [{ store: "ios", store_id: "id1693820", name: "SleepWave" }] }));
  assert.equal(links.length, 1);
  assert.equal(links[0].label, "App Store");
  assert.equal(links[0].href, "https://apps.apple.com/app/id1693820");
});

test("buildGemLinks: bare ios store_id gets an id prefix", () => {
  const links = buildGemLinks(gem({ apps: [{ store: "ios", store_id: "90211", name: "SleepWave" }] }));
  assert.equal(links[0].href, "https://apps.apple.com/app/id90211");
});

test("buildGemLinks: android store_id → play store details url", () => {
  const links = buildGemLinks(gem({ apps: [{ store: "android", store_id: "com.sleepwave.app", name: "SleepWave" }] }));
  assert.equal(links[0].label, "Play Store");
  assert.equal(links[0].href, "https://play.google.com/store/apps/details?id=com.sleepwave.app");
});

test("buildGemLinks: full set in stable order, first verified app only", () => {
  const links = buildGemLinks(
    gem({
      company: { page_id: "12345", domain: "sleepwave.app" },
      apps: [
        { store: "ios", store_id: "id90211", name: "SleepWave" },
        { store: "android", store_id: "com.ignored", name: "SleepWave Droid" },
      ],
    }),
  );
  assert.deepEqual(
    links.map((l) => l.label),
    ["Facebook page", "Website", "App Store", "Meta Ad Library"],
  );
});

test("buildGemLinks: unverified (delisted) store listings produce no store link", () => {
  // name unresolved => the store page 404s; the chip must be omitted entirely.
  const links = buildGemLinks(gem({ apps: [{ store: "android", store_id: "com.growly.imagera" }] }));
  assert.deepEqual(links.map((l) => l.label), []);
});

test("buildGemLinks: skips unverified apps[0] in favor of a verified later listing", () => {
  const links = buildGemLinks(
    gem({ apps: [
      { store: "android", store_id: "com.growly.imagera" },
      { store: "android", store_id: "com.growly.lunaspeak", name: "LunaSpeak" },
    ] }),
  );
  assert.equal(links[0].label, "Play Store");
  assert.equal(links[0].href, "https://play.google.com/store/apps/details?id=com.growly.lunaspeak");
});
