import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import Detail from './Detail.svelte';
import { blank, ingredient } from './recipe';

const identity = {user:{id:'u1',display_name:'Cook',state:'active',photo_trusted:false},device_id:'d1',admin:false,csrf_token:'csrf'};
const recipe = {...blank('structured'),id:'r1',title:'Soup',revision:2,owner_id:'u1',author_name:'Cook',can_edit:true,enrichment_status:'complete',directions:'Simmer.',ingredient_groups:[{id:'g1',name:'',ingredients:[{...ingredient(),id:'i1',name:'flour',quantity:'1',unit:'cup',original_text:'1 cup flour',grams:{amount:120,low:null,high:null,estimated:false,basis:'flour'}}]}]};

function mockApi() { const fetcher = vi.fn(async (url: string | URL, init?: RequestInit) => {
  const path = String(url);
  if (path === '/api/recipes/r1') return new Response(JSON.stringify(recipe));
  if (path === '/api/session') return new Response(JSON.stringify(identity));
  if (path === '/api/photos?recipe_id=r1' || path === '/api/photos?mine=true') return new Response(JSON.stringify({items:[]}));
  if (init?.method === 'DELETE') return new Response(null, {status:204});
  return new Response(JSON.stringify({items:[]}));
}); vi.stubGlobal('fetch', fetcher); return fetcher; }

it('keeps secondary actions subtle and confirms recipe deletion in a modal', async () => {
  const fetcher = mockApi(); const navigate = vi.fn(); render(Detail,{recipeId:'r1',navigate});
  await screen.findByRole('heading',{name:'Soup'});
  expect(screen.getByRole('button',{name:'Save for later'})).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Delete recipe'})).toBeInTheDocument();
  await fireEvent.click(screen.getByRole('button',{name:'Delete recipe'}));
  expect(screen.getByRole('dialog')).toHaveTextContent('Delete recipe');
  expect(screen.getByRole('dialog')).not.toHaveTextContent('administrator can restore');
  await fireEvent.click(within(screen.getByRole('dialog')).getByRole('button',{name:'Delete recipe'}));
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/recipes/r1?expected_revision=2', expect.objectContaining({method:'DELETE'})));
  expect(navigate).toHaveBeenCalledWith('/');
});

it('opens scaling controls and anchors original amounts to gram amounts only', async () => {
  mockApi(); render(Detail,{recipeId:'r1',navigate:vi.fn()}); await screen.findByRole('heading',{name:'Soup'});
  expect(screen.queryByText('Original & weight details')).not.toBeInTheDocument();
  await fireEvent.click(screen.getByRole('button',{name:'Adjust ingredient scale'}));
  await fireEvent.click(screen.getByRole('button',{name:'Grams'}));
  const grams = screen.getByText('120 g'); await fireEvent.mouseEnter(grams.parentElement!);
  expect(await screen.findByRole('tooltip')).toHaveTextContent('As written: 1 cup flour');
});

it('renders photos directly at the bottom instead of behind a photos toggle', async () => {
  mockApi(); render(Detail,{recipeId:'r1',navigate:vi.fn()}); await screen.findByRole('heading',{name:'Soup'});
  expect(screen.getByRole('heading',{name:'Photos'})).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:/view photos/i})).not.toBeInTheDocument();
});
