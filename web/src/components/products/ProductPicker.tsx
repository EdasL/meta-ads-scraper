"use client";
import { useId, useState, type CSSProperties } from 'react';
import { Button } from '@/components/ui';
import { createProduct, resolveProduct, type ResearchProduct, type ProductResolution } from '@/lib/api';
const inputStyle: CSSProperties = { width: '100%', minWidth: 0, boxSizing: 'border-box', padding: '9px 11px', background: 'var(--surface-2)', color: 'var(--ink)', border: '1px solid var(--line)', font: 'inherit' };
export function ProductPicker({ products, value, onChange, onCreated, disabled = false }: {
  products: ResearchProduct[]; value?: number; onChange: (id?: number) => void | Promise<void>;
  onCreated: (p: ResearchProduct) => void; disabled?: boolean;
}) {
  const id = useId();
  const [adding, setAdding] = useState(false);
  const [url, setURL] = useState('');
  const [resolved, setResolved] = useState<ProductResolution | null>(null);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [pageId, setPageId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function choose(next?: number) {
    setBusy(true); setError('');
    try { await onChange(next); } catch { setError('Could not attach this product. Please try again.'); }
    finally { setBusy(false); }
  }
  async function lookup() {
    setBusy(true); setError('');
    try {
      const result = await resolveProduct(url);
      setResolved(result); setName(result.name); setDescription(result.description);
      setPageId(result.page_id || (result.matches.length === 1 ? result.matches[0].page_id : ''));
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not look up this link.'); }
    finally { setBusy(false); }
  }
  async function save() {
    if (!resolved) return;
    setBusy(true); setError('');
    try {
      const product = await createProduct({url: resolved.url, name, description, page_id: pageId || undefined});
      onCreated(product); await onChange(product.id); setAdding(false); setURL(''); setResolved(null);
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not add product.'); }
    finally { setBusy(false); }
  }
  const product = products.find(p => p.id === value);
  return <div className="t-sm" style={{maxWidth: 650}}>
    {!adding ? <>
      <div style={{display:'flex',alignItems:'end',gap:10,flexWrap:'wrap'}}>
        <label htmlFor={`${id}-select`} style={{flex:'1 1 200px'}}>
          <span className="t-xs" style={{display:'block',marginBottom:5}}>Product</span>
          <select id={`${id}-select`} value={value ?? ''} disabled={disabled || busy} onChange={e => void choose(e.target.value ? Number(e.target.value) : undefined)} style={inputStyle}>
            <option value="">No product selected</option>
            {products.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        <Button variant="ghost" disabled={disabled || busy} onClick={() => {setAdding(true);setError('');setResolved(null);}}>Add product from link</Button>
      </div>
      {product && <div className="t-xs" style={{color:'var(--muted)',marginTop:8,overflowWrap:'anywhere'}}>
        {product.description || product.source_description ? <p style={{margin:'0 0 5px'}}>{(product.description || product.source_description).slice(0,280)}</p> : null}
        {product.url && <a href={product.url} target="_blank" rel="noreferrer" style={{textDecoration:'underline'}}>View product link</a>}
      </div>}
    </> : <form onSubmit={e => {e.preventDefault();void (resolved ? save() : lookup());}} style={{display:'grid',gap:10}}>
      <label htmlFor={`${id}-url`}>Product link</label>
      <input id={`${id}-url`} value={url} disabled={busy} onChange={e => {setURL(e.target.value);setResolved(null);}} placeholder="Website, App Store, Google Play, or Meta Ad Library link" style={inputStyle} autoFocus maxLength={2000} />
      {resolved && <>
        {resolved.matches.length > 1 && <label>Choose the matching brand
          <select value={pageId} disabled={busy} onChange={e => {setPageId(e.target.value);const m=resolved.matches.find(x=>x.page_id===e.target.value);if(m){setName(m.name);setDescription(m.description);}}} style={inputStyle}>
            <option value="">Select a brand</option>{resolved.matches.map(m => <option value={m.page_id} key={m.page_id}>{m.name || 'Unnamed brand'} · {m.page_id}</option>)}
          </select>
        </label>}
        <p className="t-xs" style={{color:'var(--muted)',margin:0}}>{resolved.name || resolved.matches.length ? 'Found existing data. Check the product details below.' : 'No matching product data yet. Add its name and what it does; it can be used without ads.'}</p>
        <label htmlFor={`${id}-name`}>Product name</label>
        <input id={`${id}-name`} value={name} disabled={busy} onChange={e=>setName(e.target.value)} required maxLength={200} style={inputStyle}/>
        <label htmlFor={`${id}-description`}>What does it help people do?</label>
        <textarea id={`${id}-description`} value={description} disabled={busy} onChange={e=>setDescription(e.target.value)} maxLength={2000} rows={3} style={{...inputStyle,resize:'vertical'}}/>
      </>}
      <div style={{display:'flex',gap:10,flexWrap:'wrap'}}>
        <Button type="submit" disabled={busy || disabled || !url.trim() || (!!resolved && (!name.trim() || (resolved.matches.length > 1 && !pageId)))}>{busy ? 'Saving…' : resolved ? 'Add and select product' : 'Look up product'}</Button>
        <Button variant="ghost" disabled={busy} onClick={()=>{setAdding(false);setError('');}}>Cancel</Button>
      </div>
    </form>}
    {error && <p role="alert" className="t-xs" style={{color:'var(--down)'}}>{error}</p>}
  </div>;
}
