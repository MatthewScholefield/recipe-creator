import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import Detail from './Detail.svelte';
import { blank, ingredient } from './recipe';
import type { Recipe } from './types';

const identity = {user:{id:'u1',display_name:'Cook',state:'active',photo_trusted:false},device_id:'d1',admin:false,csrf_token:'csrf'};
const recipe: Recipe = {...blank('structured'),id:'r1',title:'Soup',revision:2,owner_id:'u1',author_name:'Cook',can_edit:true,enrichment_status:'complete',directions:'Simmer.',ingredient_groups:[{id:'g1',name:'Ingredients',ingredients:[{...ingredient(),id:'i1',name:'flour',quantity:'1',unit:'cup',original_text:'1 cup flour',grams:{amount:120,low:null,high:null,estimated:false,basis:'flour'}}]}]};

function mockApi(value = recipe) { const fetcher = vi.fn(async (url: string | URL, init?: RequestInit) => {
  const path = String(url);
  if (path === '/api/recipes/r1') return new Response(JSON.stringify(value));
  if (path === '/api/session') return new Response(JSON.stringify(identity));
  if (path === '/api/photos?recipe_id=r1' || path === '/api/photos?mine=true') return new Response(JSON.stringify({items:[]}));
  if (init?.method === 'DELETE') return new Response(null, {status:204});
  return new Response(JSON.stringify({items:[]}));
}); vi.stubGlobal('fetch', fetcher); return fetcher; }

it('opens the editor through the detail action without relying on global link handling', async () => {
  mockApi(); const navigate = vi.fn(); render(Detail,{recipeId:'r1',navigate});
  await screen.findByRole('heading',{name:'Soup'});
  const edit = screen.getByRole('link',{name:'Edit'});
  await fireEvent.click(edit,{button:-1});
  expect(navigate).toHaveBeenCalledWith('/recipes/r1/edit');
  expect(screen.queryByRole('heading',{name:'Ingredients',level:3})).not.toBeInTheDocument();
});

it('shows the active wake-lock state with a filled accent icon', async () => {
  const request = vi.fn(async () => ({release: vi.fn(), addEventListener: vi.fn()}));
  vi.stubGlobal('navigator', {wakeLock: {request}});
  mockApi(); render(Detail,{recipeId:'r1',navigate:vi.fn()});
  await screen.findByRole('heading',{name:'Soup'});
  const control = screen.getByRole('button',{name:'Keep screen awake'});
  await fireEvent.click(control);
  expect(control).toHaveAttribute('aria-pressed','true');
  expect(control).toHaveAttribute('aria-label','Stop keeping screen awake');
  expect(control.querySelector('svg')).toHaveAttribute('fill','var(--ui-accent)');
});
it('keeps secondary actions subtle and confirms recipe deletion in a modal', async () => {
  const fetcher = mockApi(); const navigate = vi.fn(); render(Detail,{recipeId:'r1',navigate});
  await screen.findByRole('heading',{name:'Soup'});
  expect(screen.getByRole('button',{name:'Keep screen awake'}).querySelector('svg')).toHaveClass('lucide-sun');
  expect(screen.getByRole('button',{name:'Save for later'})).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Save for later'}).querySelector('svg')).toHaveAttribute('fill','none');
  expect(screen.getByRole('button',{name:'Save for later'}).querySelector('svg')).toHaveAttribute('stroke','var(--ui-accent)');
  await fireEvent.click(screen.getByRole('button',{name:'Save for later'}));
  expect(screen.getByRole('button',{name:'Remove saved recipe'}).querySelector('svg')).toHaveAttribute('fill','var(--ui-accent)');
  expect(screen.getByRole('button',{name:'Remove saved recipe'}).querySelector('svg')).toHaveAttribute('stroke','var(--ui-accent)');
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
it('flags unscalable ingredients and explains unavailable gram conversions', async () => {
  const unavailable = {...recipe,ingredient_groups:[{id:'g1',name:'Ingredients',ingredients:[
    {...ingredient(),id:'i1',original_text:'⅗ cup tapioca starch/flour, plus more for sprinkling'},
    {...ingredient(),id:'i2',quantity:'1',unit:'cup',name:'flour',original_text:'1 cup flour'},
  ]}]};
  mockApi(unavailable); const view = render(Detail,{recipeId:'r1',navigate:vi.fn()});
  await screen.findByRole('heading',{name:'Soup'});
  await fireEvent.click(screen.getByRole('button',{name:'Adjust ingredient scale'}));
  await fireEvent.click(screen.getByRole('button',{name:'2×'}));
  expect(view.container.querySelector('.scale-badge')).toHaveTextContent('2×');
  expect(screen.getByText('⅗ cup tapioca starch/flour, plus more for sprinkling')).toBeInTheDocument();
  await fireEvent.click(screen.getByRole('button',{name:'Grams'}));
  const amount = screen.getByRole('button',{name:'2 cup'});
  expect(amount).toHaveClass('unavailable');
  await fireEvent.mouseEnter(amount.closest('.tooltip-wrap')!);
  expect(await screen.findByRole('tooltip')).toHaveTextContent('No gram conversion is available');
  expect(screen.getByText('flour')).toBeInTheDocument();
});


it('shows estimated gram ranges, their explanation, and the completed recalculation control', async () => {
  const ranged = {...recipe, ingredient_groups:[{...recipe.ingredient_groups[0], ingredients:[{
    ...recipe.ingredient_groups[0].ingredients[0],
    grams:{amount:null,low:110,high:130,estimated:true,basis:'Typical flour density',assumptions:['Level cup']},
  }]}]};
  mockApi(ranged); render(Detail,{recipeId:'r1',navigate:vi.fn()}); await screen.findByRole('heading',{name:'Soup'});
  expect(screen.getByRole('button',{name:'Recalculate weight estimates'})).toBeInTheDocument();
  await fireEvent.click(screen.getByRole('button',{name:'Adjust ingredient scale'}));
  await fireEvent.click(screen.getByRole('button',{name:'Grams'}));
  const grams = screen.getByRole('button',{name:'110–130 g'});
  await fireEvent.focusIn(grams);
  expect(await screen.findByRole('tooltip')).toHaveTextContent('Basis: Typical flour density');
  expect(screen.getByRole('tooltip')).toHaveTextContent('Assumptions: Level cup');
});

it('renders photos directly at the bottom instead of behind a photos toggle', async () => {
  mockApi(); render(Detail,{recipeId:'r1',navigate:vi.fn()}); await screen.findByRole('heading',{name:'Soup'});
  expect(screen.getByRole('heading',{name:'Photos'})).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:/view photos/i})).not.toBeInTheDocument();
});
