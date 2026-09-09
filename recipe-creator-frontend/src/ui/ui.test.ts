import { fireEvent, render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import Button from './Button.svelte';
import Dropdown from './Dropdown.svelte';
import Icon from './Icon.svelte';
import Modal from './Modal.svelte';
import Spinner from './Spinner.svelte';
import Tag from './Tag.svelte';
import IconButton from './IconButton.svelte';
import TagPicker from './TagPicker.svelte';
import Tooltip from './Tooltip.svelte';
import { createRawSnippet, type Snippet } from 'svelte';
const emptySnippet = (() => '') as unknown as Snippet;
const customTrigger = createRawSnippet<[boolean]>(() => ({
  render: () => '<button type="button" aria-label="Custom profile menu">Profile</button>'
}));

it('renders a selected icon as SVG and hides unlabelled decorative icons', () => {
  const { container } = render(Icon, {name: 'search'});
  const icon = container.querySelector('svg');
  expect(icon).toBeInTheDocument();
  expect(icon).toHaveAttribute('aria-hidden', 'true');
});

it('requires an accessible name for icon-only button consumers', () => {
  render(Button, {ariaLabel: 'Save recipe', children: emptySnippet});
  expect(screen.getByRole('button', {name: 'Save recipe'})).toBeInTheDocument();
});

it('renders an icon-only route control as an accessible styled link', () => {
  render(IconButton, {href: '/saved', ariaLabel: 'Saved recipes', title: 'Saved recipes', children: emptySnippet});
  const link = screen.getByRole('link', {name: 'Saved recipes'});
  expect(link).toHaveAttribute('href', '/saved');
  expect(link).toHaveAttribute('title', 'Saved recipes');
  expect(link).toHaveClass('icon-only');
});

it('opens and closes a dropdown with escape and returns focus', async () => {
  render(Dropdown, {label: 'Profile', children: emptySnippet});
  const trigger = screen.getByRole('button', {name: 'Profile'});
  await fireEvent.click(trigger);
  expect(screen.getByRole('menu', {name: 'Profile'})).toBeInTheDocument();
  await fireEvent.keyDown(document, {key: 'Escape'});
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});

it('opens and closes a custom-trigger dropdown with escape and returns focus', async () => {
  render(Dropdown, {label: 'Profile', children: emptySnippet, trigger: customTrigger});
  const trigger = screen.getByRole('button', {name: 'Custom profile menu'});
  trigger.focus();
  await fireEvent.keyDown(trigger, {key: 'ArrowDown'});
  expect(screen.getByRole('menu', {name: 'Profile'})).toBeInTheDocument();
  await fireEvent.keyDown(document, {key: 'Escape'});
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});

it('closes modal on escape and restores focus', async () => {
  const onclose = vi.fn();
  const opener = document.createElement('button');
  document.body.append(opener); opener.focus();
  render(Modal, {open: true, title: 'Delete recipe', onclose, children: emptySnippet});
  const dialog = await screen.findByRole('dialog', {name: 'Delete recipe'});
  await fireEvent(dialog, new Event('cancel', {cancelable: true}));
  expect(onclose).toHaveBeenCalledOnce();
  expect(opener).toHaveFocus();
  opener.remove();
});

it('shows tooltip text for keyboard focus', async () => {
  render(Tooltip, {text: 'Set your name first', children: emptySnippet});
  await fireEvent.focusIn(document.querySelector('.tooltip-wrap')!);
  expect(screen.getByRole('tooltip')).toHaveTextContent('Set your name first');
});

it('has a named reduced-motion-compatible spinner', () => {
  render(Spinner, {label: 'Organizing recipe'});
  expect(screen.getByRole('status', {name: 'Organizing recipe'})).toBeInTheDocument();
});

it('searches, creates, removes, and supports single tag selection', async () => {
  const changes = vi.fn();
  render(TagPicker, {tags: ['Dinner', 'Dessert'], selected: [], label: 'Recipe tags', onchange: changes});
  const input = screen.getByRole('combobox', {name: 'Recipe tags'});
  await fireEvent.input(input, {target: {value: 'des'}});
  await fireEvent.click(screen.getByRole('button', {name: 'Dessert'}));
  expect(screen.getByText('Dessert')).toBeInTheDocument();
  await fireEvent.input(input, {target: {value: 'Quick'} });
  await fireEvent.click(screen.getByRole('button', {name: 'Create “Quick”'}));
  expect(changes).toHaveBeenLastCalledWith(['Dessert', 'Quick']);
  await fireEvent.click(screen.getByRole('button', {name: 'Remove Dessert'}));
  expect(changes).toHaveBeenLastCalledWith(['Quick']);
});

it('renders shared tags as text, links, and accessible removable buttons', async () => {
  const onclick = vi.fn();
  const { container } = render(Tag, {label: 'Dinner'});
  expect(screen.getByText('Dinner').closest('.tag')?.tagName).toBe('SPAN');
  render(Tag, {label: 'Dessert', href: '/browse?tag=Dessert', removable: true, onclick});
  const link = screen.getByRole('link', {name: 'Remove Dessert'});
  expect(link).toHaveAttribute('href', '/browse?tag=Dessert');
  expect(link.querySelector('button')).toBeNull();
  await fireEvent.click(screen.getByText('Dessert'));
  expect(onclick).not.toHaveBeenCalled();
  expect(link).not.toHaveTextContent('Dessert');
  render(Tag, {label: 'Quick', removable: true, onclick});
  await fireEvent.click(screen.getByText('Quick'));
  expect(onclick).not.toHaveBeenCalled();
  await fireEvent.click(screen.getByRole('button', {name: 'Remove Quick'}));
  expect(onclick).toHaveBeenCalledOnce();
  expect(container.querySelector('.tag svg')).toBeNull();
});

it('renders an accessible icon-only search badge', async () => {
  const onclick = vi.fn();
  render(Tag, {label: 'Search tags', iconOnly: true, expanded: false, onclick});
  const badge = screen.getByRole('button', {name: 'Search tags'});
  expect(badge).toHaveAttribute('aria-expanded', 'false');
  expect(badge).toHaveTextContent('');
  expect(badge.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
  await fireEvent.click(badge);
  expect(onclick).toHaveBeenCalledOnce();
});

it('expands compact tag search, selects multiple tags, and restores badge focus on Escape', async () => {
  const changes = vi.fn();
  render(TagPicker, {tags: ['Dinner', 'Dessert', 'Quick'], compact: true, allowCreate: false, label: 'Filter tags', onchange: changes});
  expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  await fireEvent.click(screen.getByRole('button', {name: 'Search tags'}));
  const input = screen.getByRole('combobox', {name: 'Filter tags'});
  expect(input).toHaveFocus();
  expect(screen.queryByText('Filter tags')).not.toBeInTheDocument();
  await fireEvent.click(screen.getByRole('button', {name: 'Dinner'}));
  expect(input).toHaveFocus();
  expect(screen.queryByText('Dinner')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', {name: 'Remove Dinner'})).not.toBeInTheDocument();
  await fireEvent.input(input, {target: {value: 'des'}});
  await fireEvent.click(screen.getByRole('button', {name: 'Dessert'}));
  expect(changes).toHaveBeenLastCalledWith(['Dinner', 'Dessert']);
  expect(input).toHaveValue('');
  expect(input).toHaveFocus();
  await fireEvent.keyDown(input, {key: 'Escape'});
  expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  expect(screen.getByRole('button', {name: 'Search tags'})).toHaveFocus();
});

it('collapses compact tag search when focus moves outside', async () => {
  render(TagPicker, {tags: ['Dinner'], compact: true});
  await fireEvent.click(screen.getByRole('button', {name: 'Search tags'}));
  await fireEvent.focusOut(screen.getByRole('combobox'), {relatedTarget: document.body});
  expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  expect(screen.getByRole('button', {name: 'Search tags'})).toBeInTheDocument();
});

it('replaces selection in single mode', async () => {
  const changes = vi.fn();
  render(TagPicker, {tags: ['Dinner', 'Dessert'], selected: ['Dinner'], single: true, onchange: changes});
  const input = screen.getByRole('combobox', {name: 'Tags'});
  await fireEvent.input(input, {target: {value: 'Dess'}});
  await fireEvent.click(screen.getByRole('button', {name: 'Dessert'}));
  expect(changes).toHaveBeenLastCalledWith(['Dessert']);
});
