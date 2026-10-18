# NipNip 1.1 – edible NipNip Buds for No Man's Sky

Eat your NipNip Buds and the colours of the universe start to flow.

* **NipNip Buds become edible.** The buds you harvest from NipNip plants get a **CONSUME** action in the inventory. Eating one uses it up and gives 30 seconds of free sprint.
* **The world changes colour.** For three minutes the game's own colour filters melt into each other: the weird-planet looks, hue shifts, retro palettes, corrupted-sentinel magenta and more. Each filter takes 6 seconds to blend into the next, in a new random order on every pass, then everything fades back to normal.
* **More buds, longer trip.** Every further bud adds three minutes, up to ten minutes ahead.
* **Tested in VR.** Flat mode uses the same filter system and should work the same, but was not tested.

The mod has two halves in one folder:

| Part | What it does |
| --- | --- |
| Data tables (`METADATA\...`) | Make NipNip Buds consumable and add the reward `R_NIPNIP` (30 s free sprint, the message "The colours start to flow..."). Read by the game at startup. |
| Code mod (`nip_nip.py`) | Notices when a bud is eaten and drives the colour filters. Needs [NMS.py](https://github.com/monkeyman192/NMS.py). |

Without the code mod the buds are only a sprint snack. Nothing is written to your save: the colours are changed in memory while the mod runs.

**Photosensitivity:** the colours change slowly (one blend every 6 seconds), and filters that flip light and dark are left out. If you are sensitive to changing colours or light, try it in short sessions, or not at all.

## Requirements

| What | Tested with |
| --- | --- |
| No Man's Sky, Steam, PC | build 180836 (Steam build 25732212), VR via SteamVR + Virtual Desktop |
| Python 3.13 (Setup.bat gets it for you) | 3.13.2 |
| NMS.py (`nmspy`), installs pyMHF with it | nmspy 181442.0, pyMHF 0.2.4 |

Both halves are tied to the game build. The data tables are full copies of build 180836's tables, and the code mod finds its game functions by byte patterns checked on that build. After a game update, see "After a game update" below.

## What's new in 1.1

* The code mod finds its game functions by byte patterns instead of fixed addresses, so small game updates are less likely to break it, and it says clearly in `nip_nip.log` when one is not found.
* One-click install and VR start with **Setup.bat**.
* The data tables are unchanged from 1.0.

## Install and start (one click)

1. Put this zip and **Setup.bat** (with `setup_nms_mods.ps1`, from the same download) into one folder. Don't unpack the zip. Other mod zips from this series (for example VRSit) can go into the same folder.
2. Double-click **Setup.bat**.

The first time, it installs [uv](https://docs.astral.sh/uv), sets up Python 3.13 with NMS.py in `%LOCALAPPDATA%\NMSMods`, finds No Man's Sky in your Steam libraries and configures NMS.py to start the game in VR. Every time, it installs the newest version of each mod zip in its folder into the game (the data tables into `GAMEDATA\MODS\NipNip`, the code mod into your NMS.py mod folder), makes sure `DisableAllMods` is off, starts SteamVR if needed and starts the game with the mods. Keep its window open while you play: closing it ends the game.

Later runs skip the setup and go straight to the game. `Setup.bat -Reinstall` redoes the setup; `Setup.bat -NoLaunch` only installs. If you already had NMS.py set up with another mod folder, Setup keeps that folder. To play flat instead of VR, remove the `args` line from `%APPDATA%\pymhf\nmspy\pymhf.local.toml` and start with `pymhf run nmspy`.

## Manual install

1. Install Python 3.13 from python.org (not the Microsoft Store one, not 3.14). Tick "Add python.exe to PATH".
2. `python -m pip install nmspy==181442.0`
3. Run `pymhf run nmspy` once so NMS.py asks for your mod folder (it recommends `No Man's Sky\GAMEDATA\MODS`), then close the game.
4. Copy this whole `NipNip` folder into `No Man's Sky\GAMEDATA\MODS\` (create `MODS` if it does not exist), so you have `GAMEDATA\MODS\NipNip\METADATA\REALITY\TABLES\REWARDTABLE.MBIN` and `GAMEDATA\MODS\NipNip\nip_nip.py`.
5. If your NMS.py mod folder is **not** `GAMEDATA\MODS`, also copy `nip_nip.py` into your NMS.py mod folder (or a subfolder of it).
6. Check that `Binaries\SETTINGS\GCMODSETTINGS.MXML` has `DisableAllMods` set to `false`.
7. Start: **flat:** `pymhf run nmspy`. **VR:** put a file `steam_appid.txt` containing just `275850` into `No Man's Sky\Binaries\`, add `exe = ".../Binaries/NMS.exe"` and `args = ["-HmdEnable", "1"]` to the `[pymhf.local_config]` section of `%APPDATA%\pymhf\nmspy\pymhf.local.toml`, start SteamVR and run `pymhf run nmspy`.

The game shows its usual "mods installed" notice at startup. The data tables are only read at startup, so restart the game after installing, updating or removing the mod.

## Using it

1. Harvest NipNip Buds from NipNip plants (or find them as usual).
2. Open the inventory, select the buds and hold **CONSUME** ("Exosuit nutrient system will power 30 seconds sprint overload").
3. You get "SPRINT POWER SURGE DETECTED" and "The colours start to flow...", and the colours start melting within a few seconds.
4. Eat another bud while it lasts to add three more minutes.

Each eaten bud is gone for good, and they are worth 17,776 units each.

## Tuning

The numbers at the top of `nip_nip.py` under `tunables` can be edited with any text editor. Restart the game afterwards.

| Setting | Default | Meaning |
| --- | --- | --- |
| `BUDS_SECONDS` | 180 | How long one bud lasts, in seconds. |
| `BUDS_MAX_SECONDS` | 600 | Extra buds extend the effect up to this much time ahead. |
| `FLOW_XFADE` | 6.0 | Seconds each filter takes to melt into the next. Lower is faster and wilder. |
| `BUDS_SHUFFLE` | True | A new random order on every pass. `False` keeps the order of `VIVID`. |
| `BUDS_DISTANCE` | -10000 | Negative: the whole view changes colour. A positive number of metres keeps nearby things normal and blends the colours in with distance, e.g. 250. |
| `BUDS_FADE_OUT` | 8.0 | Seconds the colours take to fade back to normal at the end. |
| `VIVID` | 16 filters | The filters a bud cycles through. Names: see below. |

The 85 filter names of build 180836 (from NMS.py's `cGcScreenFilters`): Default, DefaultStorm, Frozen, FrozenStorm, Toxic, ToxicStorm, Radioactive, RadioactiveStorm, Scorched, ScorchedStorm, Barren, BarrenStorm, Weird1–Weird8, Vintage, HyperReal, Desaturated, Amber, GBGreen, Apocalypse, Diffusion, Green, BlackAndWhite, Isolation, Sepia, Filmic, GBGrey, Binoculars, Surveying, PetSurvey, Nexus, SpaceStation, Freighter, FreighterAbandoned, Frigate, MissionSurvey, NewVibrant, NewVibrantBright, NewVibrantWarm, NewVintageBright, NewVintageWash, Drama, MemoryBold, Memory, MemoryWash, Autumn, AutumnFade, ClassicBright, Classic, ClassicWash, BlackAndWhiteDream, ColourTouchB, ColourTouchC, NegativePrint, SepiaExtreme, Solarise, TwoToneStrong, TwoTone, Dramatic, Fuchsia, Violet, Cyan, GreenNew, AmberNew, Red, HueShiftA–HueShiftD, WarmStripe, NMSRetroA–NMSRetroG, CorruptSentinels, DeepWater.

## Playing with the filters by hand

NMS.py runs Python sent to its local console (the `>>>` prompt in the window you started `pymhf run nmspy` from). There you can drive the filters directly:

```python
import nip_nip
nip_nip.show("NegativePrint", seconds=15)        # one filter, fades in and out
nip_nip.flow(xfade=3, shuffle=True)              # endless flow until off()
nip_nip.step("Weird3")                           # crossfade to one filter and keep it
nip_nip.cycle(["Weird3", "HueShiftC", "NMSRetroG"], each=5)
nip_nip.pulse("Weird6", period=4, seconds=20)    # strength breathes in and out
nip_nip.show("HueShiftC", seconds=30, distance=250)  # only far scenery changes
nip_nip.buds()                                   # same as eating a bud
nip_nip.off()                                    # fade back to normal
nip_nip.status()                                 # what each filter slot shows right now
```

## Compatibility

* The three tables **replace the game's whole tables**. Another mod that ships its own `NMS_REALITY_GCPRODUCTTABLE.MBIN`, `CONSUMABLEITEMTABLE.MBIN` or `REWARDTABLE.MBIN` conflicts: only one version is loaded and the other mod's changes (or these) are lost. Merge the edits with a tool such as AMUMSS to combine them.
* Only the vanilla item NipNip Buds is changed (`NIPNIPBUDS`: `Consumable` set to true). Its name, icon, value, stack size and its refiner and cooking uses stay the same. No recipes are added.
* The code mod uses the game's own effect slot that the binoculars and survey visor use. During a trip the trip's colours replace their tint; afterwards they work as usual.
* Tested with the code mod in a separate NMS.py mod folder (manual install step 5). Keeping `nip_nip.py` inside `GAMEDATA\MODS\NipNip` (manual step 4 with NMS.py's default mod folder) is the layout NMS.py recommends, but this exact combination was not tested.
* Removing the mod is safe for saves. Buds you ate are gone.

## After a game update

* Wait for an NMS.py release that matches the new build before playing with code mods.
* Remove the data tables (`GAMEDATA\MODS\NipNip\METADATA`) until a NipNip update for the new build is out. Old full-table copies can hide new or changed game content.
* If the game's code changed, `nip_nip.log` says "SetScreenFilter not found" or the eat hook is missing. The code mod then does nothing harmful.

## Troubleshooting

NipNip writes `nip_nip.log` next to `nip_nip.py`. A working session contains these lines:

```
NipNip: NipNip 1.1 loaded; SetScreenFilter at NMS+0x916590
NipNip: consumed NIPNIPBUDS -> reward R_NIPNIP
NipNip: start: NipNip bud: the colours start to flow
NipNip: another bud: the flow now lasts 290 s more
NipNip: the buds wear off
NipNip: effect finished, colours back to normal
```

* **No CONSUME button on the buds:** the data tables are not loaded. Check the folder layout (`GAMEDATA\MODS\NipNip\METADATA\REALITY\TABLES\...`), `DisableAllMods`, and restart the game.
* **CONSUME works, sprint and message appear, but no colours:** the code mod is not running. Check that the game was started with Setup.bat or `pymhf run nmspy` and that `nip_nip.log` exists. If it has no "consumed NIPNIPBUDS" line, NMS.py did not load the mod from your mod folder (step 5 of the manual install).
* **"SetScreenFilter not found":** the game was updated, see above.

## Uninstall

Delete `GAMEDATA\MODS\NipNip` (and `nip_nip.py` from your NMS.py mod folder if you copied it there), then restart the game.

## How it works

The game's final post-process pass blends four colour lookup tables: Base (photo mode), Far (the planet's look, set every frame), Storm (weather, every frame) and Effect (binoculars and survey modes), with Effect on top. NipNip calls the game's `cGcFullscreenEffectManager::SetScreenFilter` for the Effect slot and borrows the Storm slot as a second layer, so one filter can blend into the next with their weights always adding up to the full picture. Eating is detected in the two job functions the inventory queues when a "hold to consume" completes; they carry the consumable's reward ID.
