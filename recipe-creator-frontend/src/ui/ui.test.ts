import { fireEvent, render, screen } from '@testing-library/svelte';
import { expect, it, vi } from 'vitest';
import Button from './Button.svelte';
import Dropdown from './Dropdown.svelte';
import Modal from './Modal.svelte';
import Spinner from './Spinner.svelte';
import TagPicker from './TagPicker.svelte';
import Tooltip from './Tooltip.svelte';
import type { Snippet } from 'svelte';
const emptySnippet = (() => '') as unknown as Snippet;

it('requires an accessible name for icon-only button consumers', () => {
  render(Button, {ariaLabel: 'Save recipe', children: emptySnippet});
  expect(screen.getByRole('button', {name: 'Save recipe'})).toBeInTheDocument();
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

it('replaces selection in single mode', async () => {
  const changes = vi.fn();
  render(TagPicker, {tags: ['Dinner', 'Dessert'], selected: ['Dinner'], single: true, onchange: changes});
  const input = screen.getByRole('combobox', {name: 'Tags'});
  await fireEvent.input(input, {target: {value: 'Dess'}});
  await fireEvent.click(screen.getByRole('button', {name: 'Dessert'}));
  expect(changes).toHaveBeenLastCalledWith(['Dessert']);
});
