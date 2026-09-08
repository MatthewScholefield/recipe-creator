import { fireEvent, render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import Photos from './Photos.svelte';

const identity = {user:{id:'u1',display_name:'Cook',state:'active',photo_trusted:false},device_id:'d1',admin:false,csrf_token:'csrf'};
function mockApi(items: unknown[] = []) { return vi.stubGlobal('fetch', vi.fn(async (url: string | URL) => {
  const path = String(url);
  if (path === '/api/session') return new Response(JSON.stringify(identity));
  if (path === '/api/photos?recipe_id=r1' || path === '/api/photos?mine=true') return new Response(JSON.stringify({items}));
  return new Response(JSON.stringify({items:[]}));
})); }

it('uses a small click or drop uploader without stale explanations or refresh controls', async () => {
  mockApi(); render(Photos,{recipeId:'r1'});
  const input = await screen.findByLabelText('Add a photo');
  expect(screen.getByText('JPEG, PNG, or WebP')).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:/refresh submissions/i})).not.toBeInTheDocument();
  expect(screen.queryByText(/wait for approval|one photo at a time|resize/i)).not.toBeInTheDocument();
  await fireEvent.change(input,{target:{files:[new File(['bad'],'camera.heic',{type:'image/heic'})]}});
  expect(await screen.findByRole('alert')).toHaveTextContent('JPEG, PNG, or WebP');
});

it('keeps non-approved submissions private and only shows them to their uploader', async () => {
  mockApi([{id:'pending',recipe_id:'r1',uploader_id:'u1',uploader_name:'Cook',caption:'Dinner',state:'pending'}]);
  render(Photos,{recipeId:'r1'});
  expect(await screen.findByText('Private submission: pending')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Delete photo'})).toBeInTheDocument();
});
