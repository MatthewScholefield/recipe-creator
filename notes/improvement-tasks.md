# Recipe creator improvement tasks

Ordered roughly by implementation dependencies, with saved recipes intentionally last. The goal is a website that is simple to use, natural to navigate, easy to set up and add recipes to, and visually clean, without arbitrary wording or LLMisms.

1. The setup template currently refers to `make` commands, but the project uses `just`. We should fix those instructions to point to the actual `just` commands.

2. The source code currently uses Python's standard logger. We'd like to switch everything to `logly`, both to avoid doing `getLogger` everywhere and for its quality-of-life features. As an additional part of this task, we should add all three integrations: FastAPI, stdlib logging so output from other modules goes through the same logger, and uvicorn, using the `logly` uvicorn integration to launch the server.

3. There are a few places where we catch an exception and then neither reraise it nor log the exception, which means we never actually see the failure. We should audit these and add `logger.exception(...)` wherever we're suppressing an exception. Even turning the original error into a 502 counts here: FastAPI just returns that response to the request, so the original failure is lost unless we log it.

4. Several of the changes below need dropdowns, modals, and tooltips, and we don't want each one to end up looking or behaving differently. We should settle on shared, styled UI pieces early on, bringing in something like `radix-svelte` if needed to standardize them. We should also choose a nice icon pack for the buttons and controls across the app.

5. The user profile is currently just a link to a page, and that page doesn't look very user-profily. We should make it a profile icon in the top right that opens a dropdown containing your name, a button to go to your profile, and maybe a logout button. In the default anonymous mode, we could show a grey icon with “Anonymous” beneath it when the dropdown is open, plus a login / set name button that actually just asks for your name. We don't need to worry about syncing yet, and there should not be a weird requirement to create a recipe before setting your name.

6. The admin login is currently a separate part of the app with a single hard-coded password-style login, which feels disconnected from the user profile. We should instead gate admin capabilities on the current user having admin permission, and add a CLI tool that lists recent usernames and lets you select one to upgrade to an admin.

7. Admin recovery and ownership transfer currently start with typing an opaque recipe ID into the admin UI. We should remove those controls from there; for changing authors, an admin should instead be able to edit the author directly on a recipe by selecting someone from a filterable / searchable dropdown.

8. The app's copy currently has a very corporate, AI-generated feel, and people seeing that kind of AI slop may just walk away. We should make text like “Something good to make,” “Everyday favorites, handwritten discoveries, and recipes worth keeping,” “Our recipe notebook,” and “Good food, passed around.” configurable by the admin in the admin UI. The defaults should be much blander, like “Recipes” and “Share your food.” Bland is better than buzzwordy, and we should look for similar wording throughout the app rather than only changing these examples.

9. The UI currently explains a lot of details that aren't useful while using it, like “photos from new contributors wait for approval,” “One photo at a time,” and mentions of resizing / compressing photos. We should remove those explanations and look for similar unnecessary clutter elsewhere in the app. The supported photo file types are worth mentioning, but only very small and subtly below or inside the upload dropper.

10. Buttons around the app like Edit, Save for later, and Share are mostly plain text, and some minor actions get too much visual weight. We should give them nice icons, or make them icon-only depending on how important the action is. In particular, “Saved on this device” is a weirdly large button for such a trivial feature; we should make it a subtle bookmark icon ghost-style button instead.

11. Delete currently sits alongside everyday actions like Share and Print, and its browser confirmation includes “An administrator can restore it.” We should make Delete a small icon-only button on the recipe, only visible when you have permission to delete it. The confirmation should be a styled modal rather than the browser dialog, without the administrator-restore sentence.

12. The homepage currently shows all tags, and there are around 120, so the UI looks jank. We should show just the first few in a single row, with a search button in a similar style next to them that opens a live filter over the tags and lets you click / select one.

13. Tags are currently entered as comma-separated text even though we already have a collection of existing tags. We should use autocomplete with selectable chips and an explicit option to create a new tag, so it's easy to reuse existing tags instead of accidentally creating near-duplicates.

14. The homepage groups recipes into meal-type categories like breakfast / lunch / dinner. Most recipes don't belong to multiple of these, so we should keep the category sections rather than replace them with one grid. To avoid a recipe appearing in multiple sections, we should define a hard-coded list of meal-type classifier tags and show an error if someone chooses more than one of those for a recipe; this shouldn't prevent choosing multiple ordinary tags.

15. The search box currently advertises `tag:dessert` syntax, and searching for text replaces the selected tag rather than refining it. We should keep advanced syntax optional, show selected tags as removable filters, and let people naturally combine a tag selection with a text search.

16. Adding a recipe currently puts “Text” versus “Structured” modes front and center, which feels like choosing a data format rather than writing a recipe. We should default to text with a “Paste or write your recipe” entry point, and offer an “Organize” button as an optional next step instead of making modes a central concept.

17. Switching a recipe into its organized form currently doesn't work if you haven't set your name. We should let organizing work without a name; that action shouldn't depend on creating a profile first.

18. Loading states like organizing a recipe currently don't have a loading animation, so it's not obvious that the app is doing something. We should add loading animations there and check the other loading states around the app for the same problem.

19. When you submit a recipe or save changes without a name set, the button is still clickable but appears to do nothing, and the name prompt is easy to miss because it's at the top. We should put the prompt at the bottom near submission and disable the submit button with a tooltip explaining that you need to set your name.

20. Structured ingredient editing currently exposes quantity, range maximum, unit, ingredient name, and other separate fields for every ingredient, which is much more complicated than typing a recipe needs to be. We should completely change this to one text input per ingredient, like `2 tbsp milk`, with Enter adding the next ingredient. A backend parser should turn those lines into a simple number / unit / ingredient format. Anything it can't parse should be collected and sent together to an LLM call for parsing; for example, if it can't handle `1 1/2 tbsp milk`, the fallback should normalize that to `1.5 tbsp milk`.

21. The ingredient scaling panel's design is awkward and it's much too prominent for something you only sometimes need. We should redesign it and put the controls into a panel that opens from subtle icon-only buttons.

22. Every ingredient currently has an “Original & weight details” disclosure, which adds clutter when you're just trying to cook. We should have no option to show original / weight details in normal mode. Only in grams mode, hovering over the actual `N g` amount should show a styled tooltip containing the original amount—not hovering over the whole ingredient row.

23. The photo system is currently hidden behind a button, and the choose-photo UI is more prominent than it needs to be. We should show photos directly at the bottom of the recipe page and replace the upload control with a smaller, nicely styled dropper that you can click to upload or drag and drop onto. The “Refresh submissions” button isn't needed either; people can just reload the page if they want.

24. There's currently only one new-recipe draft slot, and its recovery prompt blocks starting another recipe until you recover or discard it. We should support multiple named drafts and let people resume them from “Add recipe” without throwing away another unfinished recipe. Your drafts should also appear at the top of the homepage with a distinct rendering that makes it clear they're your drafts and makes it easy to return to them.

25. Saved recipes currently only filters the recipes already loaded on the homepage, so it can say there are none even when you have bookmarks. We should make this a proper saved-recipes view that finds all your bookmarks without making you click “Load more.”
