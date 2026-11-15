# The menus

The game's menu screens (the title and its lists, the pause menu, Key Items, Threads of Time, the Map, the options, the save
files' dialogs): how a screen updates, how a button gets the focus, and how the pause menu's pages and Key Items find their
panes. Looked at for the menu mouse (point, click and scroll in the game's own menus).

**LS code:** `host/oot_menu.c`, `nx2/oot_patches.py`
(`patch_menu_focus`, `patch_menu_tick`)
**See also:** [layout.md](layout.md) (finding panes and their positions)

## Screens and the focus

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_802f1380` | `MenuScreen_Update(screen)` | Every menu screen's update (the HUD's, the menus', the title's), each frame, before the screen's UI controller (`screen+56`, its vtable's +16) runs the buttons' requests. Hooked by `patch_menu_tick`: the host tracks the screens and gives the mouse to the one in front | tested |
| `f_802ed660` | `PauseMenu_OnFocus(screen, pane)` | The pause menu's handler of a pane getting the focus (in its vtable at `0x84c0bc18`). Matches the pane's name against the pages (PaItemMenu_00, PaEquipmentMenu_00, PaCollectMenu_00, PaSystemMenu_00) and hands the pane to the page. Hooked by `patch_menu_focus`: the host learns the page, for its tabs | tested |
| `f_802ebd20` | `ItemsPage_OnFocus` | The Items page's part of the focus: the info panel | read |
| `f_8088ffc0` | `MenuScreen_SetFocus(screen, button, flag)` | The focus setter, as the game's screens call it. Finds the button's entry in the screen's list (`+136` the head, `+144` the first, `+156` the entry's offset from its node; `entry+24` the button) and hands it to the focus manager (`[screen+32]+96`, its vtable +48), when the manager takes the player's input | tested |
| `f_80795090` | `MenuScreen_FocusCaller` | One of the game's callers of `f_8088ffc0`, the model for how the host calls it | read |
| `f_807923a0` | `MenuPage_Name` | A page's name: `obj+128` → `+248` → `+24` (the host reads the same chain) | read |

## Key Items

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_833fdaa0` | `KeyItems_PlaceCursor` | Finds the page's items where it shows them, to place its cursor: the item database's lists 4 and 3, each entry's key and its pane | read |
| `f_833fda00` | `KeyItems_Refresh(screen, screen+760)` | The page's refresh from the key in its key buffer (`[screen+760]`, its room at `+768`): the cursor's key and the item's name and description. The page's update places the cursor from the key the next frame | tested |
| `f_80a8f690` | `ItemDb_List(db, which)` | One of the item database's lists: `+8` the count, `+16` the entries | tested |
| `f_8002a520` | `ItemEntry_Key` | An entry's key, the first of three calls (`f_8002a520`, `f_80d666a0(…, 0)`, `f_8002a4c0`) that end at a string, its text first | tested |
| `f_80d666a0` | `ItemEntry_Key2(…, 0)` | The second of the three | tested |
| `f_8002a4c0` | `ItemEntry_KeyString` | The third: the key's string | tested |
| `f_80a963c0` | `ItemEntry_PaneName` | The name of an entry's pane (a string, its text first) | tested |

## The map screen (HUD) and the pause panel's frame

The map, the song icons and a dungeon's floor widget are the HUD screen's; the pause panel's frame is the other screen of the same family. Both are `MenuScreen` subclasses built on one base constructor; see *The map screen* in Notes.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80f2ef30` | `HudScreen_New` | Allocates 0x23b0 bytes (alignment 8) and runs `f_80f2ef70` | read |
| `f_80f2ef70` | `HudScreen_Init(screen)` | HUD screen constructor: base constructor `f_8069eb40`, vtable 0x84c08300, clears its fields; floor fields `+0x1364 = -1`, `+0x1368 = 0`, `+0x136c = 0`, `+0x1370 = 0`, `+0x1374 = +0x1378 = -1` | read |
| `f_8069c4b0` | `PanelFrame_New` | Allocates 0x360 bytes (alignment 8) and runs `f_8069c4f0` | read |
| `f_8069c4f0` | `PanelFrame_Init(frame)` | Panel frame constructor: base constructor, vtable 0x84c08aa0, clears `+0x280..` fields | read |
| `f_8069eb40` | `MenuScreen_Init(screen)` | The base screen constructor shared by the HUD, the frame and about 60 other screens: zeroes `+0x20..+0x2f` (layout at `+0x28` not yet set), sets up the lists at `+0x40`, `+0x50`, `+0x88`, `+0xa0`, and `+0x138` (the buttons' list is the one at `+0x88`, see *A menu screen*) | read |
| `f_80807f00` | `HudScreen_Destruct` | Slot 0 of the HUD vtable: the complete destructor | read |
| `f_833fed30` | `HudScreen_Delete` | Slot 1 of the HUD vtable: the deleting destructor; calls the complete one, then frees 0x23b0 bytes | read |
| `f_80aa27a0` | `PanelFrame_Destruct` | Slot 0 of the frame's vtable: the complete destructor | read |
| `f_83405b40` | `PanelFrame_Delete` | Slot 1 of the frame's vtable: the deleting destructor (frees 0x360 bytes) | read |
| `f_805a3fe0` | `HudScreen_Update(screen)` | Slot 96: the HUD's per-frame class update (run by `MenuScreen_Update`). Big (about 150 KB of lifted C); runs the floor widget `f_834039a0`, compares Link's floor `f_805a3500` with `+0x1368`, drives the map and the song icons | read |
| `f_83406560` | `PanelFrame_Update(frame)` | Slot 96 of the frame: waits for its open/close fade animations to reach their last frame and then writes state code 3 or 4 into two game-state words; role guess (the overlay's fade handler) | guess |
| `f_80459830` | `Screen_CreateLayout` | Slot 89 (shared): builds the screen's layout through `f_8045a0e0`; the name is a guess, that it calls the loader is read | guess |
| `f_834039a0` | `HudMap_StepFloorState(screen)` | Runs one step of the floor widget's state machine at `+0x1358` (u32, 0..5): 0 `f_83404bf0`, 1 `f_83404c60`, 2 `f_83404cc0` (the floor selector), 3..5 `f_83404ac0`; called from `f_805a3fe0` | read |
| `f_83404cc0` | `HudMap_StepFloor(screen)` | The floor selector: when the request word `+0x12c` has up bits (0x10000 / 0x100000) raises `+0x1368` by one, with down bits (0x20000 / 0x200000) lowers it by one, only inside `[+0x1370, +0x1370+count-1]`; when byte `+0x12c` bit 4 is set it re-initialises (state 3); ends by refreshing the marks (`f_83403f20`) | read |
| `f_80b482c0` | `HudMap_CountFloors(screen)` | Recomputes `+0x1370` (lowest floor) and `+0x136c` (floor count) from the floor bitmask at `+0x1500` and the map info `[screen+0x390]` (`+0x37c` floors, `+0x384` base floor id), fills the bits between the lowest and highest, and sets the count animation `[screen+0x2d8]` to count-1 | read |
| `f_805a3500` | `HudMap_LinkFloor(screen)` | The floor id of Link's room in the shown map (-99: none); the update compares it with `+0x1368` | guess |
| `f_83403f20` | `HudMap_MarkFloorSlots(screen)` | After a floor change: lights the floor mark whose index equals `floor - lowest` among up to five slots (`[screen+0x388]`, count `+0x380`) | guess |
| `f_80b48380` | `HudMap_Open(screen)` | Sets the map widget up for a map: calls `f_80b482c0` and the other floor/pane setup (`f_80b48710`, `f_80b48ac0`, `f_80b49240`, `f_80b48dc0`, `f_80b486c0`, `f_80b4bed0`, `f_80b487a0`) | guess |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84c0bc18` | `PauseMenu_vtable` | The pause menu's vtable (holds `f_802ed660`) | read |
| `0x84c9e0d0` | `g_ItemDbHolder` | The item database, `[[0x84c9e0d0]]`. The host's pouch calls load the same holder ([player.md](player.md)) | tested |
| `0x84c08300` | `HudScreen_vtable` | The vtable of the HUD screen (object 0x23b0 bytes; layout resource `LayoutMsg/MapMenu_00`, the data word after slot 106). Slot 17 `f_802f1380`, slot 89 `f_80459830`, slot 96 `f_805a3fe0` | read |
| `0x84c08aa0` | `PanelFrame_vtable` | The vtable of the pause panel's frame screen (object 0x360 bytes; layout resource `LayoutMsg/MenuOverlay_00`). Slot 17 `f_802f1380`, slot 96 `f_83406560` | read |
| `0x84c0d098` | `UIShortCutBGScreen_vtable` | The vtable of the item-wheel (shortcut) background screen, by the class its factory makes (the name is from the port's `nx2/oot_wide.py`, which keeps this screen whole under the wide-UI mask: it redraws a capture of the whole picture) | guess |
| `0x84c04fb0` | `UICommonScreen_vtable` | The vtable of the pause and system menu backgrounds' screen, by the class its factory makes (named in `nx2/oot_wide.py`, which keeps it whole under the wide-UI mask) | guess |
| `0x84c061c8` | `UIEventFadeScreen_vtable` | The vtable of the event fade screen, by the class its factory makes (named in `nx2/oot_wide.py`; the fade covers the whole picture) | guess |
| `0x84c07b30` | `UILoadingFadeScreen_vtable` | The vtable of the loading fade screen, by the class its factory makes (named in `nx2/oot_wide.py`): the wide-UI projection hook recognises the screen by it; its canvas is at `screen+0x1b8`, and the port follows its layout holder to the loading-cover pane tree | guess |
| `0x84c0f4a0` | `UISystemOverlayScreen_vtable` | The vtable of the system overlay screen, by the class its factory makes (named in `nx2/oot_wide.py`, the "also kept" screen: squeezed, the item quick select's background went into the middle 16:9 from its first opening on) | guess |

## Structures

### A menu screen

| Offset | Field | Sure |
|---|---|---|
| `+32` | An object whose `+96` is the focus manager | tested |
| `+40` | The screen's layout | tested |
| `+56` | The UI controller (its vtable +16 runs the buttons' requests) | read |
| `+136`, `+144`, `+156` | The buttons' list: the head, the first, an entry's offset from its node | tested |

### The Key Items page

| Offset | Field | Sure |
|---|---|---|
| `+760` | The key buffer's pointer (`+768` its room) | tested |
| `+840` | 0 while the page browses, 1 while an item's details show | tested |

### The HUD screen (vtable `0x84c08300`, 0x23b0 bytes)

Its base part is *A menu screen* above (`+0x20` the focus holder, `+0x28` the layout, `+0x38` the UI controller).

| Offset | Field | Sure |
|---|---|---|
| `+0x00` | u64: the vtable `0x84c08300` | read |
| `+0x108` | object: a bit set cleared by `f_83403a70` (bits 0x10000, 0x100000, 0x20000, 0x200000) | guess |
| `+0x12c` | u32: request/edge bits: 0x10000 or 0x100000 = floor up, 0x20000 or 0x200000 = floor down; bit 4 of the byte = (re)initialise the floor widget | read |
| `+0x2d8` | u64: animation object of the floor-count display (frame = count-1) | read |
| `+0x388` / `+0x380` | u64 / u32: the floor mark slots (array pointer, count) | guess |
| `+0x390` | u64: the shown map's info object: `+0x37c` s32 floor count, `+0x384` s32 base floor id, `+0x360` s32 current map index | read |
| `+0x1358` | u32: floor widget state 0..5 (dispatch `f_834039a0`; 2 = the selector `f_83404cc0`) | read |
| `+0x135c` | u32: widget sub-state (0..3; read by `f_83404bf0`, `f_83403a70`) | guess |
| `+0x1364` | s32: -1 at construction; role not determined | read |
| `+0x1368` | s32: **floor shown** (changed only by `f_83404cc0`) | read |
| `+0x136c` | s32: **floor count** (written by `f_80b482c0`) | read |
| `+0x1370` | s32: **lowest floor id** (written by `f_80b482c0`) | read |
| `+0x1374`, `+0x1378` | s32, s32: -1 at construction; role not determined | read |
| `+0x1420` | char[0x40]: an inline string (the current map/room name) with its length at `+0x1428`; used by `f_805a3500` | guess |
| `+0x1500` | u8: bitmask of the dungeon's floors (bit i = floor i) | read |
| `+0x1480`, `+0x1498`, `+0x14d0` | u64: pane pointers the map widget keeps (`f_80b49ce0` reads them to place the picture and its icons) | read |

## Notes

- `HOS_MENU_TRACE=1` logs the focus changes, the screens and what the mouse does.


### The map screen

- **Two classes of one family.** The HUD (vtable `0x84c08300`, 0x23b0 bytes, allocated by `f_80f2ef30`, layout resource `LayoutMsg/MapMenu_00`) and the pause panel's frame (vtable `0x84c08aa0`, 0x360 bytes, `f_8069c4b0`, `LayoutMsg/MenuOverlay_00`) have 107-slot vtables that are identical except slots 0 to 3, 46, 47, 51, 57, 58, 65, 93, 94, 96, 98, 102, 104 and 106 and a data word after the last slot: a pointer to the layout resource's name. That the word names the layout is a guess; the strings are read. Slot 0 is the complete destructor, slot 1 the deleting one (it frees with the object's size), slot 2 a type test and slot 3 the class's type descriptor. **read**
- **Slot 96 is the per-frame class update.** `MenuScreen_Update` (`f_802f1380`, slot 17, shared) calls it once an update unless the class overrides slot 37 or 95; neither screen does (both hold the base stubs `f_83be9990` and `f_83418b50`, which `f_802f1380` compares against the two global cells `0x84caa980` and `0x84caa9b0`). Slot 89 (shared, `f_80459830`) builds the layout. **read**
- **The screen's layout is at `screen+0x28`:** the HUD's own pane helpers (`f_833fe640`, `f_83404880`) take it from there, as the host's `item_panes` does with `f_8078e2b0`. The base constructor zeroes `+0x20..+0x2f`, so the pointer is stored when the layout is built, by code not found. **read**
- **The floor widget.** `+0x1368` is the floor shown, `+0x136c` the floor count and `+0x1370` the lowest floor, all signed 32-bit. `f_80b482c0` scans the floor bitmask at `+0x1500` (bit i is floor i of the dungeon) over the first `[mapinfo+0x37c]` bits, with `mapinfo = [screen+0x390]`, finds the lowest and highest set bits, stores `+0x1370 = [mapinfo+0x384] + lowest bit` (the dungeon's base floor id plus the bit) and `+0x136c = highest - lowest + 1`, and sets the floor-count animation (`[screen+0x2d8]`) to frame count - 1. `+0x1368` changes only in the stepper `f_83404cc0`: one floor up or down on the request word's bits (`+0x12c`: 0x10000 or 0x100000 up, 0x20000 or 0x200000 down), and only while `lowest <= floor <= lowest + count - 1`. So the top floor is `lowest + count - 1`, as `host/oot_menu.c` assumes; "2F = 1, 1F = 0, B1 = -1" is data (base floor id plus the bit), not code. The constructor sets `+0x1364 = -1`, `+0x1368`, `+0x136c` and `+0x1370` to 0 and `+0x1374` and `+0x1378` to -1. **read**
- **Link's floor.** `f_805a3500` returns the floor id of the room Link is in (-99 when none) and the update compares it with `+0x1368`. **read** (the -99 and the comparison), **guess** (the name)
- **The panes of the map's pictures** (`P_ReplaceField_00`, `P_ReplaceFieldMini_00`, `P_ReplaceFieldMiniFront_00`, `P_ReplaceDungeon_00`, `P_ReplaceDungeonMini_00`, `P_MapBg_00`, `N_PatternField_00`, `N_PatternDungeon_00`, `N_PatternFieldMini_00`, `N_PatternDungeonMini_00`) have their names in one string table at `0x83f36290`. No function among the ~37 that touch that page names them and no pointer table holds them: the HUD keeps its panes as pointers in its own fields (for example `+0x1380`, `+0x1398`, `+0x14d0`), so finding them by name on the layout is how an outsider does it. **read**

## Open questions

- Which of the two screens' layouts holds `N_PatternField_00`, `N_PatternDungeon_00`, `P_ReplaceDungeonMini_00` and `P_MiniMapFrame_00`: call `f_8078e2b0` once with the layout at `[hud+0x28]` and once at `[frame+0x28]`; expected non-zero from the HUD (the Map page's pictures are in the HUD's layout, an assumption of the other co-op project, not seen here).
- In a three-floor dungeon read `[hud+0x1368]`, `+0x136c` and `+0x1370` at the base floor and after one UP; expected `(lowest, 3, lowest)` then `(lowest+1, 3, lowest)`. `host/oot_menu.c` already assumes it.
- Who stores `[screen+0x28]`; what slots 46, 47, 51, 57, 58, 65, 93, 94, 98, 102, 104 and 106 of the two vtables do (they differ between the HUD and the frame; slot 93 resolves blackboard-like lookups, `f_833fed60`); what `+0x1364`, `+0x1374`, `+0x1378` and `+0x137c` (initially 30) of the HUD are; which of `+0x1480`, `+0x1498` and `+0x14d0` is the minimap picture; what `f_83406560` is for beyond waiting for fade animations and writing 3 or 4 into two game-state words.