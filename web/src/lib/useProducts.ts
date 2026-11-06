"use client";
import { useEffect, useState } from 'react';
import { useSession } from 'next-auth/react';
import { getProducts, type ResearchProduct } from './api';
export function useProducts() {
  const { data: session } = useSession();
  const email = session?.user?.email;
  const [state, setState] = useState<{email?: string | null; products: ResearchProduct[]; error: string}>({products: [], error: ''});
  useEffect(() => {
    if (!email) return;
    let cancelled = false;
    getProducts().then(products => { if (!cancelled) setState({email, products, error: ''}); })
      .catch(() => { if (!cancelled) setState({email, products: [], error: 'Could not load products. Refresh to try again.'}); });
    return () => { cancelled = true; };
  }, [email]);
  return {
    products: state.email === email ? state.products : [],
    error: state.email === email ? state.error : '',
    addProduct: (product: ResearchProduct) => setState(s => ({email, error: '', products: [product, ...s.products.filter(p => p.id !== product.id)]})),
  };
}
