# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-10-26

### Added

- Run profiles (Debug › Run profiles, Ctrl+Shift+R): opponent, stage, AI, palette, life, power, rounds and time.
- `charfactory run --profile NAME` uses the profiles saved in the app.
- Run in MUGEN or Ikemen GO with the unsaved edits, without saving.
- Sprites: drag the sprite to align it (Shift locks the direction).
- Stage background in sprites and animations: simple stage, a MUGEN stage or a PNG.
- Timeline: drag a frame edge to change its time, Shift+drag to stretch the whole animation, drag a frame to reorder it, right-click for more.
- States: parameter window for every controller, with defaults, options and what each one does (Ctrl+Shift+Space).
- Parameter descriptions in Portuguese and Spanish, and the Ikemen GO parameters explained.
- Code: find bar with Ctrl+F (regex, replace); Ctrl+Shift+G goes to a statedef, command or animation.
- 12 palettes step by step: pick what changes, styles and intensity, then choose among the ideas.
- New palette styles: light and dark clothes, tones of one color, complementary, metals, nature and fighting game classics.
- Simulator: damage of the move, power gained and P2 life tick by tick.
- Import images: palette embedded in the image, recommended mode, scale and "fit to the character's height".
- Palettes: import a palette from an image (PNG, PCX, GIF, BMP).
- AI: tools for images, palettes and run profiles, quick actions and "Ask the AI to fix this move".

### Fixed

- Simulator draws ticks several times faster and big zoomed or rotated sprites no longer vanish.
- A tick that fails to draw shows the error instead of stalling playback.
- The animation preview next to States no longer freezes the window.
- Importing or editing a very large image no longer freezes the program.
- Helper and Explod were shown as deprecated.
- Generating palettes keeps the old ones (moved to 13 and up) and the sprites that use them.
- Light and black clothes no longer stay the same in every generated palette.
- Cleaner side panels in animations, sprites, sounds and the simulator.

## [0.3.0] - 2026-10-21

### Added

- **Import images** window (Add image, Add several images, dropping images on the window and
  pasting), with a preview of how each image will look:
  group and first index; the colors from the character palette (1,1), from any palette in the
  SFF, from an **own palette** made from the image (one per image or one for the whole batch, up
  to N colors; an indexed image that fits keeps its palette) or as **true colors** (32-bit PNG,
  for images with more colors than a palette holds; MUGEN 1.1 and Ikemen); optional dithering;
  and the transparent background (automatic, alpha, top-left corner, index 0 of the image or a
  picked color), which always goes to index 0 of a new palette. A note warns when the colors
  change a lot in the chosen palette. Before, every image was remapped to palette 1,1.
- Import images: group and index from the file name
  (`kfm_200-3.png`), numbering in steps of 1 or 10, the axis by 9 anchors or exact X/Y (a yellow
  cross in the preview), **Keep the indices and use the palette** (swap) besides adapting the
  image, a reversed palette (last color as background), reusing an identical palette already in
  the SFF and choosing the number of the new one, **Don't crop the image**, and images equal to a
  sprite that already exists becoming a link to it (always, never or ask).

- Sprite editor, Palettes panel: **All palettes in the SFF**, besides the character palettes
  (1,x): the palette slider goes through every palette in the file.
  Picking one shows a sprite that uses it, the grid edits that exact palette (even when the SFF
  repeats the same number) and the text links to every sprite that uses it, or says none does.
- Palette editor: **Color effects**, in two lines: **Turn** one color into
  another (blue into red and so on; only the tones of that color change, skin, grays and the rest
  stay) and an **Effect** list (grayscale, invert, sepia, lighter, darker, more and less
  saturated, swapping or rotating the red, green and blue channels). They act on the selected
  colors or the whole palette, each one an Undo step.
- AI: **build_move** makes a whole move from a list of steps, without writing CNS. The agent
  says what each step does (strike, launcher, jump, air strike, air finisher, finisher, dash) and
  which animation it uses; the tool writes one statedef per step, each one chained only if the
  step before it hit, with enough hitstun for the next step, the launch, the jump after the
  opponent and the landing, plus the command and the [Statedef -1] entry. It then tunes launch
  height, jump strength and timing in the simulator until every hit connects, and says which
  step misses and why when one does. The jump is added on its own, and an animation with no
  attack box gets a copy with one. Calling it again with the same state changes the move.
  The move follows the character's own style, read from its other moves: its hit and guard
  sounds, its sparks (the Explod or Helper its normals create on a hit, with sparkno = -1) and
  its SuperPause. Every ground step closes in to the opponent, and in a step with several hits
  only the last one launches or knocks down.
- AI: guide topic **style**, what to do and what to avoid when writing moves by hand, measured on
  260 JUS-style characters: helpers instead of the Projectile controller, the character's own
  sparks and sounds, SuperPause without the engine's flash, the Explod and HitDef values most of
  them use, and effect angles, facing and scale (angles written facing right, since the engine
  mirrors them; facing by postype; varied sparks; uniform scale; the character's own scale
  factor). The projectiles and supers topics now teach the same.
- **Ctrl+F searches the open tab**: animations by number or name, sprites by group or
  group,item, sounds by group or group,number, statedefs by number or comment, commands by name
  or input, [Statedef -1] entries by the state they go to, and states in the simulator; the
  exact number comes first. In the code tabs, finding the text in the file is the last item of
  the list. Ctrl+H and the Definitions tab keep the text search.

### Changed

- AI assistant: much shorter path to a move. The recipe sends the agent to build_move after one
  look at the animations, instead of reading the guide, other moves and dozens of animations
  first; the guide is no longer sent on every step (mugen_guide still has it); the editor note
  about what is on screen is used only when the user points at it; pictures already seen are not
  sent again (Gemini, OpenAI); Gemini thinks at the low level on each step (models without levels
  fall back on their own); and after eight read-only calls in a row the agent is reminded to act.

- **Slice sprite sheet**: the sheet fills the window, with zoom (wheel, buttons, Ctrl+0/Ctrl+9,
  zoom to the selected box) and panning (middle button or Space), and the boxes are edited right
  on it: drag one to move it, its handles to resize, the yellow cross to move the axis, drag on an
  empty spot to add one, Del removes, arrows nudge, and X/Y/W/H/axis fields for exact values,
  with Undo/Redo. The detection runs by itself at each setting (no more Show preview button),
  without discarding boxes edited by hand; **Hide the background** shows what will be cut, the
  background color can be picked on the sheet, and the settings are remembered.
- One **Palette editor** (Ctrl+Shift+P) replaces the advanced palette editor and the separate
  "12 palettes by color bands" window. A strip at the top shows the character in each of its
  palettes 1–12 (an empty slot is a **+** that leads to making it); below are the color grid, the
  preview with the character's poses and three tabs: **Colors** (the color tools), **New palette**
  (hue, saturation and brightness with the preview showing the result before saving) and
  **12 palettes** (color bands with locks, a scheme that generates palettes 2–12, and adjusting one
  band in one palette, applied at once). The sprite editor's Palettes panel opens it on the
  right tab.
- Palette editor: editing a color repaints the sprite at each color tried (Cancel puts it back),
  clicking the sprite selects its color in the grid and **Blink the selected colors** shows where
  they are (or where the chosen band is). The preview only lists sprites drawn with the character
  palette.
- Palette editor: **Undo**/**Redo** buttons and Ctrl+Z/Ctrl+Y inside the editor (it opens in its
  own window, which the main window's Ctrl+Z did not reach, so a tool like Grayscale could only be
  reverted by reloading from disk), and **Back to how it was** returns every palette to how it was
  when the editor opened. Each palette tool is its own undo step, also in the Palettes panel of
  the sprite editor.
- Simulator timeline: sounds, effects and screen events triggered by `AnimElem = N`,
  `AnimElemTime(N) = M` or `!Time` (with or without a `triggerall`) can be dragged to another tick,
  not only those with `trigger1 = Time = N`; a frame trigger stays a frame trigger. Dropping one in
  the hit pause says that it plays when the pause ends, and the tooltip of an event that cannot be
  dragged says why. The cursor is an open hand (closed while dragging) instead of the resize
  arrows.
- States: an **Organize** choice at the top of the state tree, also in View › State tree:
  **Smart** (grouped by what each state is) or **Classic** (every state by number). The old file-order list opens as
  Classic.
- Open characters: one button on the main bar with the current character (and how many others
  are open) and a menu to switch, open another or close, instead of tabs that shrank and cut the
  names when several were open.
- Palette editor: it always opens on the sprite on screen (the frame's sprite on the Animations
  page) with that sprite's default palette, whatever palette the panel had picked; a sprite with
  its own palette (like 3,0 of an effect) opens on that palette.
- 12 palettes: two new schemes. **Themes** gives each palette its own look (shadow, gold, ice,
  toxic, royal, fire, ocean, ghost, pastel, military, neon), tinting white and gray bands too, and
  the strip shows each name; **Surprise** draws color harmonies again on each click. The bands now
  take every sprite into account (colors used only after the first 120 sprites never changed).
- 12 palettes: choose which palettes to generate (from/to) and **Only empty slots** to keep the
  ones already made; with **Also fix the .def**, the buttons in `[Palette Keymap]` (when some
  palette could not be picked, like every button on palette 1) and `pal.defaults` (so the engine
  does not repeat palette 1 for the CPU or a mirror match) are set too, in the same undo step.
- Palette editor: a note at the top says when the .def keeps palettes from being picked in the
  game, with a **Fix in the .def** button. **Back to how it was** also puts the .def back.
- The .def is now part of Undo/Redo, like sprites and states.
- Cleaner palette icons: the painter's palette is drawn with curves, and the 12 palettes icon has
  room between the swatches.
- States, Smart view: a **Helpers** group gathers the states that helpers run (the `stateno` of
  each Helper and the states they change to), whatever their number; states the character also
  goes to stay with the character.
- Sprite editor, Palettes panel: picking a palette shows the sprite with it (it used to jump back
  to the sprite's palette), and a sprite with its own palette (like 3,0 of an effect) shows and
  edits that one, with a note when the character palettes do not change it.

### Fixed

- Unused resources: sounds played with `PlaySnd value = S35,7`, animations picked as
  `1060 + (random % 3)`, sparks from `sparkno = S7000`, the KO sound 11,0, get-hit sprites
  5000–5999 (used by the opponent's throws) and screenpack sprites 9000–9999 no longer appear as
  unused. Resources a computed expression may reach (`7800 + var(3)`) go to a separate
  **Maybe used** group.
- Ikemen GO: `stcommon = common1.cns` finds `data/common1.cns.zss`, as the engine does.
- Simulator: an Explod whose animation ends in a frame of time 0 (common in hit sparks) now goes
  away with `removetime = -2`; before it stayed to the end, and so did the helper waiting for
  `!NumExplod` to destroy itself.
- Simulator: `const(size.xscale)` and the other `size.*` constants inside a helper are the ones the
  Helper controller gave it, not the character's (sparks of a helper drawn 2.5× too big).
- Simulator: invisible helpers (sprite -1, often parked at 999,999) no longer widen the view,
  which shrank the characters into a corner.
- Simulator: `Trans = sub` on the character or a helper, `alpha = s,d` of Explod and Trans with
  `addalpha`, `Sub` in the AIR and the destination part of `AS<n>D<m>` are drawn as the engine
  does; `Trans` only lasts the tick it runs.
- Palettes 1,2 and up: **Clear colors no sprite uses** blanked the whole palette, and swapping,
  sorting or moving colors in one character palette left the others with colors out of place.

## [0.2.5] - 2026-10-21

### Added

- Run in Ikemen GO: **Debug → Run in MUGEN / Run in Ikemen GO**, an Ikemen GO folder and an engine
  choice in **Tools → Options** (Automatic runs in the engine of the folder the character is in;
  outside one, ZSS characters go to Ikemen GO), and `charfactory run --engine ikemen`. Ikemen GO
  loads a character outside its `chars/` by its full path, no copy needed; its console output
  (warnings about the character) and crash log show in the Engine output panel, and a `Motif` in
  `save/config.ini` that does not exist is reported before the match closes on it.

### Fixed

- Command tester (`test_command`): the `b` button was read as back, and `6 6` / `4 4` did not
  count as a dash.
- Validator: `ModifyExplod` and `AppendToClipboard` take the parameters of `Explod` and
  `DisplayToClipboard`, as the MUGEN documentation says, instead of reporting each as unknown.

## [0.2.0] - 2026-10-20

### Added

- MCP server built into the editor: Claude Code, Claude Desktop, Codex, Gemini CLI, Cursor and other
  MCP clients can read and edit the open character (states, commands, animations, constants,
  sprites, simulator frame data), with every change undoable. Local only (`127.0.0.1`) and
  protected by a token; set up in **AI → Connect an agent (MCP)**. Agents get a guide to how
  MUGEN works (physics, sides, HitDef, projectiles, throws, commands) so they can write any move,
  a one-call starting point for a new attack, animations with boxes fitted to each sprite, a
  contact sheet and names for every animation, the simulator's path (where the character goes and
  from which side a move hits), a test of which state an input reaches, the controller reference,
  and checks that refuse AIR boxes the parser would drop, inputs another move already uses and
  overwriting an existing move by accident. `get_move` shows a move's statedef, animations, input
  and the states that chain into it in one call; `edit_state` and `edit_animation` change single
  parameters, frames, timing or boxes in place instead of rewriting the whole block. State and
  animation lists are one line each with filters, and repeated problems come grouped, so a large
  character costs a fraction of the tokens.
- AI assistant panel (Ctrl+Shift+A) with the same tools, using an Anthropic, OpenAI or Google
  Gemini API key, or a local model through an OpenAI-compatible endpoint (Ollama, LM Studio,
  OpenRouter). API keys are stored in the Windows Credential Manager. Each message tells the model
  what you are looking at (the statedef under the cursor, the animation and frame, the sprite or
  the simulated state), and Claude runs with prompt caching and adaptive thinking. The panel shows
  each step in plain words (click one for what was sent and returned), images you can enlarge, what
  the AI changed at the end of each answer, and errors that say what to do, with **Try again**.
- `charfactory mcp [char.def]`: the MCP server over stdio, connected to the open window when its
  server is on; the executable accepts `--mcp` for the same.

### Fixed

- A move made in the Move Wizard went to the end of `[Statedef -1]`, where a normal attack on the
  same button took the input first and the move never came out; it now goes right before that
  entry. Its controllers are also numbered after the statedef instead of `[State 0, …]`.
- The move transplant summary showed "comandos" untranslated in every language.
- The simulator's problem list showed internal Portuguese codes ("sem-acerto: …") before each
  message; it now shows only the translated message, and the codes in the JSON output are English
  (`no-hit`, `below-ground`, `huge-effect`, `dark-halo`, `reversed-push`).
- The Simulator page started a jumping attack entered from the ground at the top of a jump, so it
  missed there; it now starts on the ground, like the AI tools' simulation.

## [0.1.0] - 2026-10-20

### Added

- First public release: sprite, palette, animation, sound and code (CNS, CMD, AIR, DEF) editors,
  tick-by-tick move simulator, stage editor, tools and the `charfactory` command line.
- Interface in English, Spanish and Brazilian Portuguese.

[Unreleased]: https://github.com/eduardovbe/CharFactory/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/eduardovbe/CharFactory/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/eduardovbe/CharFactory/compare/v0.2.5...v0.3.0
[0.2.5]: https://github.com/eduardovbe/CharFactory/compare/v0.2.0...v0.2.5
[0.2.0]: https://github.com/eduardovbe/CharFactory/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/eduardovbe/CharFactory/releases/tag/v0.1.0
