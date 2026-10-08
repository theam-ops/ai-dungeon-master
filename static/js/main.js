/* The front end's entry point. No framework, no build step: the browser loads these
   ES modules as they are. Each module owns one part of the page; this one loads them
   all - in the order the single app.js used to run - and then starts the app. */

import "./i18n/index.js";
import "./core/dom.js";
import "./core/state.js";
import "./core/api.js";
import "./lang.js";
import "./boot.js";
import "./login.js";
import "./create.js";
import "./join.js";
import "./transfer.js";
import "./library.js";
import "./notes.js";
import "./guide.js";
import "./gallery.js";
import "./ai.js";
import "./images.js";
import "./campaign.js";
import "./stream.js";
import "./composer.js";
import "./party.js";
import "./dash.js";
import "./drawer.js";
import "./map.js";
import "./sound.js";
import "./voice.js";

import { boot } from "./boot.js";
import { enterCampaign } from "./campaign.js";
import { show } from "./core/dom.js";
import { S } from "./core/state.js";
import { showDash } from "./dash.js";
import { openDrawer, showDrawerTab } from "./drawer.js";
import { applyI18n, setLang } from "./i18n/index.js";
import { refreshUI } from "./lang.js";
import { measureMood, soundState } from "./sound.js";
import { handle } from "./stream.js";

showDash(localStorage.getItem("dash") !== "0");

/* For the console, and for the frontend equivalence check in docs/REFACTORING_PLAN.md:
   with modules, nothing is global any more. */
window.__dm = { S, handle, enterCampaign, openDrawer, showDrawerTab, refreshUI, show,
                setLang, applyI18n, soundState, measureMood };

boot();
