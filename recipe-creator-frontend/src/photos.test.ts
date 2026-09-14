import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { beforeEach, expect, it, vi } from 'vitest';
import Photos from './Photos.svelte';

const compress = vi.hoisted(() => vi.fn(async (file: File) => file));
vi.mock('browser-image-compression', () => ({default: compress}));

const identity = {user:{id:'u1',display_name:'Cook',state:'active',trusted:false},device_id:'d1',admin:false,csrf_token:'csrf'};
function mockApi(items: unknown[] = [], upload?: (init: RequestInit) => Response) {
  const fetcher = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    const path = String(url);
    if (path === '/api/session') return new Response(JSON.stringify(identity));
    if (path === '/api/photos?recipe_id=r1' || path === '/api/photos?mine=true') return new Response(JSON.stringify({items}));
    if (path === '/api/recipes/r1/photos' && init?.method === 'POST') return upload?.(init) ?? new Response(JSON.stringify({}), {status:201});
    return new Response(JSON.stringify({items:[]}));
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}

beforeEach(() => {
  compress.mockClear();
  vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockReturnValue('data:image/webp;base64,');
  Object.defineProperties(URL, {
    createObjectURL: {value: vi.fn(() => 'blob:preview'), configurable: true},
    revokeObjectURL: {value: vi.fn(), configurable: true},
  });
});

it('rejects unsupported photo files without loading a compressor or uploading', async () => {
  const fetcher = mockApi();
  render(Photos,{recipeId:'r1'});
  const input = await screen.findByLabelText('Add a photo');
  expect(screen.getByText('JPEG, PNG, or WebP')).toBeInTheDocument();
  await fireEvent.change(input,{target:{files:[new File(['bad'],'camera.heic',{type:'image/heic'})]}});
  expect(await screen.findByRole('alert')).toHaveTextContent('Choose a JPEG, PNG, or WebP image.');
  expect(compress).not.toHaveBeenCalled();
  expect(fetcher.mock.calls.some(([,init]) => init?.method === 'POST')).toBe(false);
});

it('keeps non-approved submissions private and shows metadata without requiring a note', async () => {
  mockApi([{id:'pending',recipe_id:'r1',uploader_id:'u1',uploader_name:'Cook',caption:'',state:'pending',created_at:null}]);
  render(Photos,{recipeId:'r1'});
  expect(await screen.findByText('Private submission: pending')).toBeInTheDocument();
  expect(screen.getByText('From Cook')).toBeInTheDocument();
  expect(screen.getByText('Upload date unavailable')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Delete photo'})).toBeInTheDocument();
});

it('retains a failed draft, reuses its key, and rotates the key after note edits', async () => {
  const uploads: RequestInit[] = [];
  mockApi([], init => { uploads.push(init); return new Response(JSON.stringify({detail:'Upload unavailable'}),{status:503}); });
  render(Photos,{recipeId:'r1'});
  const input = await screen.findByLabelText('Add a photo');
  await fireEvent.change(input,{target:{files:[new File(['png'],'meal.png',{type:'image/png'})]}});
  const note = await screen.findByLabelText('Photo note (optional)');
  await fireEvent.input(note,{target:{value:'Used oat milk.\n\nIt stayed creamy.'}});

  await fireEvent.click(screen.getByRole('button',{name:'Submit photo'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('Upload unavailable');
  expect(note).toHaveValue('Used oat milk.\n\nIt stayed creamy.');
  expect(screen.getByAltText('Your contribution, ready to upload')).toHaveAttribute('src','blob:preview');

  await fireEvent.click(screen.getByRole('button',{name:'Submit photo'}));
  await waitFor(() => expect(uploads).toHaveLength(2));
  const firstKey = new Headers(uploads[0].headers).get('Idempotency-Key');
  expect(new Headers(uploads[1].headers).get('Idempotency-Key')).toBe(firstKey);

  await fireEvent.input(note,{target:{value:'Used oat milk.\n\nIt stayed especially creamy.'}});
  await fireEvent.click(screen.getByRole('button',{name:'Submit photo'}));
  await waitFor(() => expect(uploads).toHaveLength(3));
  expect(new Headers(uploads[2].headers).get('Idempotency-Key')).not.toBe(firstKey);
});

it('counts Unicode code points and blocks notes above 5,000 characters', async () => {
  mockApi();
  render(Photos,{recipeId:'r1'});
  await fireEvent.change(await screen.findByLabelText('Add a photo'),{target:{files:[new File(['png'],'meal.png',{type:'image/png'})]}});
  const note = await screen.findByLabelText('Photo note (optional)');
  await fireEvent.input(note,{target:{value:'😀' + 'a'.repeat(4999)}});
  expect(screen.getByText('5,000 / 5,000 characters')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Submit photo'})).toBeEnabled();
  await fireEvent.input(note,{target:{value:'😀' + 'a'.repeat(5000)}});
  expect(screen.getByText('Photo notes must be at most 5,000 characters.')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Submit photo'})).toBeDisabled();
});
