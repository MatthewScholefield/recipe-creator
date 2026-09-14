import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import Detail from './Detail.svelte';
import { blank, ingredient } from './recipe';
import type { Recipe } from './types';

const identity = {user:{id:'u1',display_name:'Cook',state:'active',trusted:false},device_id:'d1',admin:false,csrf_token:'csrf'};
const recipe: Recipe = {...blank('structured'),id:'r1',title:'Soup',revision:2,owner_id:'u1',author_name:'Cook',can_edit:true,enrichment_status:'complete',total_views:0,unique_viewers:0,directions:'Simmer.',ingredient_groups:[{id:'g1',name:'Ingredients',ingredients:[{...ingredient(),id:'i1',name:'flour',quantity:'1',unit:'cup',original_text:'1 cup flour',grams:{amount:120,low:null,high:null,estimated:false,basis:'flour'}}]}]};

function mockApi(value = recipe) { const fetcher = vi.fn(async (url: string | URL, init?: RequestInit) => {
  const path = String(url);
  if (path === '/api/recipes/r1') return new Response(JSON.stringify(value));
  if (path === '/api/recipes/r1/views') return new Response(JSON.stringify({total_views:value.total_views,unique_viewers:value.unique_viewers}));
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

it('shows one gram amount and puts the original amount in its tooltip', async () => {
  mockApi(); render(Detail,{recipeId:'r1',navigate:vi.fn()}); await screen.findByRole('heading',{name:'Soup'});
  expect(screen.queryByText('Original & weight details')).not.toBeInTheDocument();
  await fireEvent.click(screen.getByRole('button',{name:'Adjust ingredient scale'}));
  expect(screen.getByRole('menu',{name:'Ingredient settings'})).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Adjust ingredient scale'}).querySelector('.lucide-settings')).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'Reset checks'})).not.toBeInTheDocument();
  await fireEvent.click(screen.getByRole('tab',{name:'Grams'}));
  const grams = screen.getByText('120 g'); await fireEvent.mouseEnter(grams.parentElement!);
  expect(await screen.findByRole('tooltip')).toHaveTextContent('As written: 1 cup flour Weight: 120 g');
});
it('keeps detail, fallback, scaling, and authored text consistent', async () => {
  const quantities = {
    ...recipe,
    ingredient_groups:[{
      id:'g1',
      name:'Ingredients',
      ingredients:[
        {...ingredient(), id:'half', name:'flour', quantity:'0.5', unit:'cup',
          original_text:'½ cup flour from family notes',
          grams:{amount:60, low:null, high:null, estimated:false, basis:'fixed fixture'}},
        {...ingredient(), id:'fifth', name:'sugar', quantity:'0.6', unit:'cup',
          original_text:'⅗ cup sugar', grams:null},
        {...ingredient(), id:'third', name:'salt', quantity:'0.33333333333333333',
          unit:'tsp', original_text:'1/3 tsp salt', grams:null},
        {...ingredient(), id:'metric', name:'yeast', quantity:'0.5', unit:'g',
          original_text:'0.5 g yeast', grams:null},
        {...ingredient(), id:'range', name:'oil', quantity:'0.25', quantity_max:'0.75',
          unit:'tablespoons', original_text:'1/4–3/4 tablespoons oil', grams:null},
        {...ingredient(), id:'legacy', name:'starch', quantity:'⅗', unit:'cup',
          original_text:'⅗ cup starch', grams:null},
      ],
    }],
  };
  mockApi(quantities);
  const view = render(Detail,{recipeId:'r1',navigate:vi.fn()});
  await screen.findByRole('heading',{name:'Soup'});
  for (const text of ['½ cup flour', '0.6 cup sugar', '⅓ tsp salt', '0.5 g yeast',
                      '¼–¾ tablespoons oil', '0.6 cup starch']) {
    expect(view.container).toHaveTextContent(text);
  }

  await fireEvent.click(screen.getByRole('button',{name:'Adjust ingredient scale'}));
  await fireEvent.click(screen.getByRole('tab',{name:'Custom'}));
  await fireEvent.input(screen.getByLabelText('Custom multiplier'),{target:{value:'3'}});
  expect(view.container).toHaveTextContent('1 ½ cup flour');
  expect(view.container).toHaveTextContent('1.8 cup sugar');
  expect(view.container).toHaveTextContent('1 tsp salt');
  expect(view.container).toHaveTextContent('1.5 g yeast');
  expect(view.container).toHaveTextContent('¾–2 ¼ tablespoons oil');

  await fireEvent.click(screen.getByRole('tab',{name:'Grams'}));
  const weight = screen.getByText('180 g');
  await fireEvent.mouseEnter(weight.parentElement!);
  expect(await screen.findByRole('tooltip')).toHaveTextContent(
    'As written: ½ cup flour from family notes',
  );
  expect(screen.getAllByText('1.8 cup')).toHaveLength(2);
  expect(screen.getAllByText('1.8 cup')[0]).toHaveClass('unavailable');
  expect(screen.getByText('sugar')).toBeInTheDocument();
});
it('flags unscalable ingredients and explains unavailable gram conversions', async () => {
  const unavailable = {...recipe,ingredient_groups:[{id:'g1',name:'Ingredients',ingredients:[
    {...ingredient(),id:'i1',original_text:'⅗ cup tapioca starch/flour, plus more for sprinkling'},
    {...ingredient(),id:'i2',quantity:'1',unit:'cup',name:'flour',original_text:'1 cup flour',
      grams:{amount:null,low:null,high:null,estimated:true,basis:'Insufficient identity',refusal_reason:'The ingredient type is not specific enough to choose a density.'}},
  ]}]};
  mockApi(unavailable); const view = render(Detail,{recipeId:'r1',navigate:vi.fn()});
  await screen.findByRole('heading',{name:'Soup'});
  await fireEvent.click(screen.getByRole('button',{name:'Adjust ingredient scale'}));
  await fireEvent.click(screen.getByRole('tab',{name:'2×'}));
  expect(view.container.querySelector('.scale-badge')).toHaveTextContent('2×');
  expect(screen.getByText('⅗ cup tapioca starch/flour, plus more for sprinkling')).toBeInTheDocument();
  await fireEvent.click(screen.getByRole('tab',{name:'Grams'}));
  const amount = screen.getByText('2 cup');
  expect(amount).toHaveClass('unavailable');
  await fireEvent.mouseEnter(amount.parentElement!);
  expect(await screen.findByRole('tooltip')).toHaveTextContent('No gram conversion: The ingredient type is not specific enough to choose a density.');
  expect(screen.getByText('flour')).toBeInTheDocument();
});


it('shows a recommended gram estimate with uncertainty and details in its tooltip', async () => {
  const ranged = {...recipe, ingredient_groups:[{...recipe.ingredient_groups[0], ingredients:[{
    ...recipe.ingredient_groups[0].ingredients[0],
    grams:{amount:null,low:110,high:130,estimated:true,basis:'Typical flour density',assumptions:['Level cup']},
  }]}]};
  mockApi(ranged); render(Detail,{recipeId:'r1',navigate:vi.fn()}); await screen.findByRole('heading',{name:'Soup'});
  expect(screen.queryByRole('button',{name:/Recalculate|Retry weight/})).not.toBeInTheDocument();
  await fireEvent.click(screen.getByRole('button',{name:'Adjust ingredient scale'}));
  await fireEvent.click(screen.getByRole('tab',{name:'Grams'}));
  const grams = screen.getByText('120 g');
  await fireEvent.mouseEnter(grams.parentElement!);
  const tooltip = await screen.findByRole('tooltip');
  expect(tooltip).toHaveTextContent('As written: 1 cup flour Estimate: 120 ± 10 g');
  expect(tooltip.textContent).toContain('As written: 1 cup flour\nEstimate: 120 ± 10 g');
  expect(tooltip).toHaveTextContent('Basis: Typical flour density');
  expect(tooltip).toHaveTextContent('Assumptions: Level cup');
});

it('renders photos directly at the bottom instead of behind a photos toggle', async () => {
  mockApi(); render(Detail,{recipeId:'r1',navigate:vi.fn()}); await screen.findByRole('heading',{name:'Soup'});
  expect(screen.getByRole('heading',{name:'Photos'})).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:/view photos/i})).not.toBeInTheDocument();
});

it('registers a visible detail once and exposes exact clickable statistics', async () => {
  const counted = {...recipe, total_views:1234, unique_viewers:567};
  const fetcher = mockApi(counted);
  render(Detail,{recipeId:'r1',navigate:vi.fn()});
  const statistics = await screen.findByRole('button',{name:'View statistics: 1234 total views, 567 unique viewers'});
  await waitFor(() => expect(fetcher.mock.calls.filter(([url]) => String(url) === '/api/recipes/r1/views')).toHaveLength(1));
  expect(statistics).toHaveTextContent('1.2K views');
  await fireEvent.click(statistics);
  expect(screen.getByRole('tooltip').textContent).toBe('Total views: 1,234\nUnique viewers: 567');
  expect(statistics).toHaveAttribute('aria-expanded','true');
  await fireEvent.click(statistics);
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  expect(statistics).toHaveAttribute('aria-expanded','false');
  await fireEvent.focusOut(statistics,{relatedTarget:document.body});
  statistics.focus();
  await fireEvent.focusIn(statistics);
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
  await fireEvent.keyDown(document,{key:'Escape'});
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  expect(statistics).toHaveFocus();
  await fireEvent.click(statistics);
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
  await fireEvent.pointerDown(document.body);
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  await fireEvent.click(statistics);
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
  await fireEvent.click(statistics);
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
});

it('waits for a hidden detail to become visible before registering', async () => {
  let visibility: DocumentVisibilityState = 'hidden';
  vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility);
  const fetcher = mockApi();
  const view = render(Detail,{recipeId:'r1',navigate:vi.fn()});
  await screen.findByRole('heading',{name:'Soup'});
  expect(fetcher.mock.calls.some(([url]) => String(url) === '/api/recipes/r1/views')).toBe(false);
  visibility = 'visible';
  document.dispatchEvent(new Event('visibilitychange'));
  await waitFor(() => expect(fetcher.mock.calls.filter(([url]) => String(url) === '/api/recipes/r1/views')).toHaveLength(1));
  view.unmount();
  document.dispatchEvent(new Event('visibilitychange'));
  expect(fetcher.mock.calls.filter(([url]) => String(url) === '/api/recipes/r1/views')).toHaveLength(1);
});

it('does not update or log when unmounted before registration completes', async () => {
  const pending = (Promise as PromiseConstructor & {withResolvers<T>(): {promise: Promise<T>; resolve(value: T): void; reject(reason?: unknown): void}}).withResolvers<Response>();
  const logged = vi.spyOn(console,'error').mockImplementation(() => {});
  const fetcher = vi.fn(async (url: string | URL) => {
    const path = String(url);
    if (path === '/api/recipes/r1') return new Response(JSON.stringify(recipe));
    if (path === '/api/session') return new Response(JSON.stringify(identity));
    if (path === '/api/recipes/r1/views') return pending.promise;
    return new Response(JSON.stringify({items:[]}));
  });
  vi.stubGlobal('fetch',fetcher);
  const component = render(Detail,{recipeId:'r1',navigate:vi.fn()});
  await screen.findByRole('heading',{name:'Soup'});
  await waitFor(() => expect(fetcher.mock.calls.some(([url]) => String(url) === '/api/recipes/r1/views')).toBe(true));
  component.unmount();
  pending.resolve(new Response(JSON.stringify({total_views:99,unique_viewers:99})));
  await pending.promise;
  await Promise.resolve();
  expect(logged).not.toHaveBeenCalled();
});
