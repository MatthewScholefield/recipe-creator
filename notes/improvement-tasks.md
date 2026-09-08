# Recipe creator improvement tasks

Ordered roughly by implementation dependencies, with saved recipes intentionally last. The goal is a website that is simple to use, natural to navigate, easy to set up and add recipes to, and visually clean, without arbitrary wording or LLMisms.

1. Fix the setup instructions/template to use `just` instead of the nonexistent `make` commands.

2. Switch to logly and integrate it with FastAPI and stdlib logging for all modules that produce output in our code. Avoid having to do `getLogger` everywhere. Use the logly integration for uvicorn to launch uvicorn too.

3. Audit places where we catch an exception without reraising it or logging the exception. Use `logger.exception(...)` wherever we suppress a failure so we actually see it. Turning an exception into a 502 also counts as suppressing it: FastAPI just returns the response and we lose the original error unless we log it.

4. Standardize our dropdowns, styled modals, and tooltips before adding more of them. If needed, bring in something like radix-svelte for these shared UI pieces. Choose a nice icon pack too, so buttons and controls look consistent across the app.

5. The user profile is currently just a link to a page that doesn't look very user-profily. Make it a profile icon in the top right with a dropdown containing your name, a button to go to your profile, and maybe a logout button. In the default anonymous mode, show a grey icon, “Anonymous” beneath it when the dropdown is open, and a login / set name button that just asks for your name. Don't worry about syncing yet. You should not have to create a recipe before setting your name.

6. Replace the separate, hard-coded admin password login with admin capabilities gated on the current user having admin permission. Add a CLI tool that lists recent usernames and lets you select one to upgrade to an admin.

7. Remove the recipe-ID-based recovery / ownership-transfer controls from the admin UI. Instead, let an admin edit a recipe's author directly on the recipe using a filterable / searchable author dropdown.

8. Get rid of the corporate, AI-generated-sounding copy throughout the app. Things like “Something good to make,” “Everyday favorites, handwritten discoveries, and recipes worth keeping,” “Our recipe notebook,” and “Good food, passed around.” should be configurable by the admin in the admin UI. Use much blander defaults like “Recipes” and “Share your food.” Bland is better than buzzwordy; people should not open the app and immediately see AI slop.

9. Audit the rest of the UI for unnecessary explanations and implementation details. Don't show things like “photos from new contributors wait for approval,” “One photo at a time,” or explanations about resizing / compressing photos. Look for similar clutter elsewhere too. Keep the supported photo file types, but show them very small and subtly below or inside the upload dropper.

10. Buttons like Edit, Save for later, Share, etc. should have nice icons, or be icon-only depending on how important the action is. “Saved on this device” is much too prominent for such a trivial feature; make it a subtle bookmark icon ghost-style button instead.

11. Keep Delete as an icon-only button on the recipe, shown only if you have permission to delete it. Replace the browser confirmation with a styled modal, and remove “An administrator can restore it.” from the confirmation message.

12. The homepage currently shows all ~120 tags, which looks jank. Show only the first few in a single row, with a similarly styled search button next to them that opens a live tag filter and lets you click / select a tag.

13. Tags in the editor should use autocomplete with selectable chips and an explicit option to create a new tag, instead of a comma-separated text input. Make it easy to reuse existing tags rather than accidentally creating near-duplicates.

14. Keep the homepage's meal-type category sections rather than replacing them with one grid. Define a hard-coded list of meal-type classifier tags, like breakfast / lunch / dinner, and show an error if someone chooses more than one of those tags for a recipe.

15. Stop advertising `tag:dessert` syntax in the search box. Keep advanced syntax optional, show selected tags as removable filters, and let a text search refine the selected tag rather than replace it.

16. Default recipe entry to text, with an “Organize” button to organize the ingredients. Start with “Paste or write your recipe” rather than making people choose between “Text” and “Structured” data formats as a central part of adding a recipe.

17. Organizing a recipe should work even if you haven't set your name. Remove the current name requirement from that action.

18. Add loading animations for actions like organizing a recipe, and audit other loading states around the app so it's obvious when something is happening.

19. When submitting a new recipe or saving changes without a name set, the button is clickable but appears to do nothing because the name prompt is at the top. Put the name prompt at the bottom near the submit button, and disable submission with a tooltip explaining that you need to set your name.

20. Completely simplify structured ingredient editing: each ingredient should just be a single text input like `2 tbsp milk`, with Enter adding the next ingredient. Use a backend parser to turn the lines into a simple number / unit / ingredient format instead of exposing quantity, range maximum, unit, and other separate fields. Collect all ingredients the parser can't handle and send them together to an LLM call for parsing. For example, if the parser can't handle `1 1/2 tbsp milk`, the fallback should normalize it to `1.5 tbsp milk`.

21. The ingredient scaling panel looks bad and is much too prominent. Redesign it and move the controls into a panel opened with subtle icon-only buttons.

22. Remove the per-ingredient “Original & weight details” disclosures and the option to show originals in normal mode. Only in grams mode, hovering over the actual `N g` amount should show a styled tooltip containing the original amount; don't make the whole ingredient row a tooltip target.

23. Show photos directly at the bottom of the recipe page instead of hiding the photo system behind a button. Replace the choose-photo UI with a smaller, nicely styled dropper that supports both clicking to upload and dragging and dropping. Remove “Refresh submissions”; people can reload the page if they want.

24. Support multiple named new-recipe drafts instead of one draft slot that you must recover or discard before starting another recipe. Let people resume drafts from “Add recipe,” and also show their drafts at the top of the homepage with a distinct rendering that makes it clear they're your drafts and easy to return to.

25. Saved recipes currently only filters the recipes already loaded on the homepage, so it can say there are none even when you have bookmarks. Make this a proper saved-recipes view that finds all your bookmarks without making you click “Load more.”
