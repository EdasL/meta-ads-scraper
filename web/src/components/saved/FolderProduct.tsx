"use client";
import { useState } from 'react';
import { Button, ButtonLink } from '@/components/ui';
import { ProductPicker } from '@/components/products/ProductPicker';
import { useProducts } from '@/lib/useProducts';
import type { SavedGroup } from '@/lib/saved';
export function FolderProduct({ folder, onSave }: { folder: SavedGroup; onSave: (id: number, productId?: number) => Promise<void> }) {
  const {products, error, addProduct} = useProducts();
  const [editing, setEditing] = useState(false);
  const product = products.find(p => p.id === folder.product_id);
  return <div style={{marginBottom:22,paddingBottom:18,borderBottom:'1px solid var(--line)'}}>
    {editing ? <>
      <p className="t-xs" style={{color:'var(--muted)',margin:'0 0 12px'}}>Brands saved in this folder will be references for the selected product.</p>
      <ProductPicker products={products} value={folder.product_id ?? undefined} onCreated={addProduct} disabled={folder.id <= 0} onChange={async id=>{await onSave(folder.id,id);setEditing(false);}}/>
      <div style={{marginTop:10}}><Button variant="ghost" onClick={()=>setEditing(false)}>Done</Button></div>
    </> : <div style={{display:'flex',alignItems:'center',flexWrap:'wrap',gap:12}}>
      <div style={{flex:'1 1 240px',minWidth:0}}>
        <div className="eyebrow" style={{color:'var(--muted)',marginBottom:5}}>Product</div>
        <div className="t-sm" style={{overflowWrap:'anywhere'}}>{product?.name || folder.product || (folder.product_id ? 'Loading product…' : 'Attach the product these saved brands are for.')}</div>
      </div>
      <Button variant="ghost" disabled={folder.id<=0} onClick={()=>setEditing(true)}>{folder.product_id ? 'Change product' : 'Attach product'}</Button>
      {folder.product_id && <ButtonLink href={`/hunt?folder=${folder.id}`}>Hunt for this product</ButtonLink>}
    </div>}
    {error && <p role="alert" className="t-xs" style={{color:'var(--down)'}}>{error}</p>}
  </div>;
}
