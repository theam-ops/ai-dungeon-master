/* The browser's state for the campaign it is in. Part of the front end - see js/main.js. */

export const S = {
  campaign: null,      // {id, code, name, lang, you}
  party: [],
  lastSeq: 0,
  seen: new Set(),
  es: null,            // EventSource
  live: null,          // narration element currently being streamed into
  pending: null,       // campaign being joined, awaiting character choice
  options: null,
  optionsLang: null,
  providers: [],       // every AI the server could use
  canSetKeys: false,   // may this browser paste a key straight into the server?
  backend: null,       // the one running this campaign
  picked: { race: null, class: null, scores: null },
  combat: null,        // the fight in progress, as the server stores it, or null
  notes: "",           // your own standing notes for the DM
  notesMax: 600,       // replaced by the server's real cap when a campaign is entered
  attached: [],        // images staged for the next action
  lastScene: "",       // newest narration, for "illustrate this scene"
  rolls: [],           // newest-last, for the HUD; only the last few are kept
  hudOpen: localStorage.getItem("hud") !== "0",
};
