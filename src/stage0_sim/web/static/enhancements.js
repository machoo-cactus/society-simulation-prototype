const LIVE_TARGETS = [
  "#run-status",
  "#run-summary-region",
  "#world-heading-detail",
  "#world-render",
  "#inspector-live",
  "#event-summary",
  "#event-live",
  "#transcript-live",
];

const PAGE_TARGETS = [
  "#notice-region",
  "#simulation-controls",
  "#world-panel",
  "#inspector-panel",
  "#events-panel",
  "#transcript-panel",
  "#dataset-panel",
];

function pageTargetSelectors() {
  const configured = document.body.dataset.uiPageTargets || "";
  const selectors = configured
    .split(",")
    .map((selector) => selector.trim())
    .filter(Boolean);
  return selectors.length ? selectors : PAGE_TARGETS;
}

let actionInFlight = false;
let liveTimer = null;
let liveController = null;
let zoomTimer = null;
let mapInteractionActive = false;
const activatedSubmitters = new WeakMap();

function targetSelectors(element) {
  return element.dataset.uiTargets
    .split(",")
    .map((selector) => selector.trim())
    .filter(Boolean);
}

function removableQueryKeys(element) {
  return (element.dataset.uiRemoveQuery || "")
    .split(",")
    .map((key) => key.trim())
    .filter(Boolean);
}

function mergedUrl(destination, values, removedKeys = []) {
  const current = new URL(window.location.href);
  const requested = new URL(destination, current);
  const result =
    requested.origin === current.origin && requested.pathname === current.pathname
      ? new URL(current)
      : requested;
  for (const key of removedKeys) result.searchParams.delete(key);
  for (const [key, value] of values) result.searchParams.set(key, value);
  result.hash = requested.hash;
  return result;
}

function describeFocus(element) {
  if (!(element instanceof HTMLElement) || element === document.body) return null;
  const form = element.closest("form");
  return {
    id: element.id,
    name: element.getAttribute("name"),
    label: element.getAttribute("aria-label"),
    text: element.textContent?.trim() || "",
    tag: element.tagName.toLowerCase(),
    formAction: form?.getAttribute("action") || "",
  };
}

function focusDescriptor() {
  return describeFocus(document.activeElement);
}

function restoreFocus(descriptor) {
  if (!descriptor) return;
  let candidate = descriptor.id
    ? document.getElementById(descriptor.id)
    : null;
  if (!candidate && descriptor.name) {
    const scope = descriptor.formAction
      ? [...document.querySelectorAll("form")].find(
          (form) => form.getAttribute("action") === descriptor.formAction,
        )
      : document;
    candidate = scope?.querySelector(`[name="${CSS.escape(descriptor.name)}"]`);
  }
  if (!candidate && (descriptor.label || descriptor.text)) {
    const candidates = document.querySelectorAll(descriptor.tag);
    candidate = [...candidates].find(
      (element) =>
        (descriptor.label &&
          element.getAttribute("aria-label") === descriptor.label) ||
        (descriptor.text && element.textContent?.trim() === descriptor.text),
    );
  }
  if (candidate instanceof HTMLElement && !candidate.hasAttribute("disabled")) {
    candidate.focus({ preventScroll: true });
  }
}

function interactionSnapshot() {
  const scroll = new Map();
  for (const element of document.querySelectorAll("[data-preserve-scroll][id]")) {
    scroll.set(element.id, {
      left: element.scrollLeft,
      top: element.scrollTop,
    });
  }
  return {
    details: [...document.querySelectorAll("details[open] summary")].map(
      (summary) => summary.textContent?.trim() || "",
    ),
    focus: focusDescriptor(),
    scroll,
    windowX: window.scrollX,
    windowY: window.scrollY,
  };
}

function restoreInteraction(snapshot, hash = "") {
  for (const [id, position] of snapshot.scroll) {
    const element = document.getElementById(id);
    if (element) {
      element.scrollLeft = position.left;
      element.scrollTop = position.top;
      element.dataset.restoredScroll = "true";
    }
  }
  for (const summary of document.querySelectorAll("details summary")) {
    if (snapshot.details.includes(summary.textContent?.trim() || "")) {
      summary.parentElement.open = true;
    }
  }
  restoreFocus(snapshot.focus);
  if (hash) {
    document.querySelector(hash)?.scrollIntoView();
  } else {
    window.scrollTo(snapshot.windowX, snapshot.windowY);
  }
}

function setBusy(selectors, busy) {
  for (const selector of selectors) {
    const target = document.querySelector(selector);
    if (target) {
      if (busy) {
        target.setAttribute("aria-busy", "true");
        target.inert = true;
      } else {
        target.removeAttribute("aria-busy");
        target.inert = false;
      }
    }
  }
}

function replaceTargets(
  selectors,
  stateDocument,
  noticeDocument = stateDocument,
  snapshot = interactionSnapshot(),
) {
  for (const selector of selectors) {
    const current = document.querySelector(selector);
    const sourceDocument =
      selector === "#notice-region" ? noticeDocument : stateDocument;
    const replacement = sourceDocument.querySelector(selector);
    if (!current || !replacement) {
      throw new Error(`Partial UI target is missing: ${selector}`);
    }
    current.replaceWith(replacement.cloneNode(true));
  }
  document.body.dataset.liveRefresh =
    stateDocument.body.dataset.liveRefresh || "false";
  restoreInteraction(snapshot);
  setupMaps();
}

async function htmlDocument(response) {
  if (!response.ok) {
    throw new Error(`UI request failed with HTTP ${response.status}`);
  }
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("text/html")) {
    throw new Error(`Expected HTML but received ${contentType || "unknown content"}`);
  }
  return new DOMParser().parseFromString(await response.text(), "text/html");
}

async function fetchPage(url, options = {}) {
  return htmlDocument(
    await fetch(url, {
      cache: "no-store",
      credentials: "same-origin",
      ...options,
    }),
  );
}

async function updateFromForm(form, submitter) {
  const selectors = targetSelectors(form);
  const method = (form.method || "get").toUpperCase();
  const formAction = form.getAttribute("action") || window.location.href;
  const formData = new FormData(form);
  if (submitter?.name) formData.append(submitter.name, submitter.value);
  const snapshotUrl = new URL(window.location.href);
  const removedKeys = removableQueryKeys(form);
  for (const key of removedKeys) snapshotUrl.searchParams.delete(key);
  const interaction = interactionSnapshot();
  interaction.focus = describeFocus(submitter) || interaction.focus;
  actionInFlight = true;
  liveController?.abort();
  setBusy(selectors, true);
  try {
    let stateDocument;
    let noticeDocument;
    let navigationUrl = null;
    if (method === "GET") {
      navigationUrl = mergedUrl(
        formAction,
        formData.entries(),
        removableQueryKeys(form),
      );
      stateDocument = await fetchPage(navigationUrl);
      noticeDocument = stateDocument;
    } else {
      const actionDocument = await fetchPage(formAction, {
        method,
        body: formData,
      });
      noticeDocument = actionDocument;
      const responseUrl = new URL(window.location.href);
      stateDocument =
        responseUrl.pathname === new URL(formAction, window.location.href).pathname &&
        responseUrl.search === snapshotUrl.search
          ? actionDocument
          : await fetchPage(snapshotUrl);
    }
    replaceTargets(selectors, stateDocument, noticeDocument, interaction);
    if (method !== "GET" && removedKeys.length) {
      history.replaceState(null, "", snapshotUrl);
    }
    if (navigationUrl) {
      history.pushState(null, "", navigationUrl);
      if (navigationUrl.hash) {
        document.querySelector(navigationUrl.hash)?.scrollIntoView();
      }
    }
  } finally {
    setBusy(selectors, false);
    actionInFlight = false;
    scheduleLiveRefresh();
  }
}

async function updateFromLink(link) {
  const selectors = link.dataset.uiLinkTargets
    .split(",")
    .map((selector) => selector.trim())
    .filter(Boolean);
  const requested = new URL(link.getAttribute("href"), window.location.href);
  const destination = mergedUrl(
    requested,
    requested.searchParams.entries(),
    removableQueryKeys(link),
  );
  const interaction = interactionSnapshot();
  actionInFlight = true;
  liveController?.abort();
  setBusy(selectors, true);
  try {
    const stateDocument = await fetchPage(destination);
    replaceTargets(selectors, stateDocument, stateDocument, interaction);
    history.pushState(null, "", destination);
    if (destination.hash) document.querySelector(destination.hash)?.scrollIntoView();
  } finally {
    setBusy(selectors, false);
    actionInFlight = false;
    scheduleLiveRefresh();
  }
}

function scheduleLiveRefresh() {
  if (liveTimer) window.clearTimeout(liveTimer);
  liveTimer = null;
  if (document.body.dataset.liveRefresh === "true") {
    liveTimer = window.setTimeout(refreshLiveRegions, 1000);
  }
}

async function refreshLiveRegions() {
  liveTimer = null;
  const editing = document.activeElement?.matches(
    "input, select, textarea, [contenteditable=true]",
  );
  if (
    actionInFlight ||
    mapInteractionActive ||
    editing ||
    document.hidden
  ) {
    scheduleLiveRefresh();
    return;
  }
  liveController = new AbortController();
  try {
    const stateDocument = await fetchPage(window.location.href, {
      signal: liveController.signal,
    });
    replaceTargets(LIVE_TARGETS, stateDocument);
  } catch (error) {
    if (error.name !== "AbortError") {
      console.error("Live UI refresh failed", error);
    }
  } finally {
    liveController = null;
    scheduleLiveRefresh();
  }
}

function clampZoom(value) {
  return Math.min(3, Math.max(0.5, value));
}

function mapCamera(viewport, map) {
  const viewportBounds = viewport.getBoundingClientRect();
  const mapBounds = map.getBoundingClientRect();
  const x =
    (viewportBounds.left + viewport.clientWidth / 2 - mapBounds.left) /
    Math.max(1, mapBounds.width);
  const y =
    (viewportBounds.top + viewport.clientHeight / 2 - mapBounds.top) /
    Math.max(1, mapBounds.height);
  return {
    x: Math.min(1, Math.max(0, x)),
    y: Math.min(1, Math.max(0, y)),
  };
}

function centerFollowedCharacter(viewport, map) {
  if (map.dataset.followSelected !== "true") return;
  const marker = map.querySelector(".character-marker.selected, .city-character.selected");
  if (!(marker instanceof SVGCircleElement)) return;
  const markerBounds = marker.getBoundingClientRect();
  const viewportBounds = viewport.getBoundingClientRect();
  viewport.scrollLeft +=
    markerBounds.left + markerBounds.width / 2 -
    (viewportBounds.left + viewportBounds.width / 2);
  viewport.scrollTop +=
    markerBounds.top + markerBounds.height / 2 -
    (viewportBounds.top + viewportBounds.height / 2);
}

function synchronizeZoom(zoom, viewport, map) {
  if (zoomTimer) window.clearTimeout(zoomTimer);
  zoomTimer = window.setTimeout(async () => {
    zoomTimer = null;
    try {
      const camera = mapCamera(viewport, map);
      const endpoint = viewport.dataset.mapZoomEndpoint || "/ui/view/zoom";
      const response = await fetch(endpoint, {
        method: "POST",
        cache: "no-store",
        credentials: "same-origin",
        headers: {"Content-Type": "application/x-www-form-urlencoded"},
        body: new URLSearchParams({
          zoom: zoom.toFixed(4),
          camera_x: camera.x.toFixed(6),
          camera_y: camera.y.toFixed(6),
        }),
      });
      if (!response.ok) {
        throw new Error(`Zoom synchronization failed with HTTP ${response.status}`);
      }
      const stateDocument = await fetchPage(window.location.href);
      const refreshTargets = (
        viewport.dataset.mapRefreshTarget ||
        "#world-panel,#inspector-panel"
      )
        .split(",")
        .map((selector) => selector.trim())
        .filter(Boolean);
      replaceTargets(
        refreshTargets,
        stateDocument,
      );
    } catch (error) {
      console.error("Map zoom synchronization failed", error);
    } finally {
      mapInteractionActive = false;
      scheduleLiveRefresh();
    }
  }, 150);
}

function applyWheelZoom(viewport, event) {
  const map = viewport.querySelector("[data-map-zoom]");
  if (!(map instanceof SVGElement)) return;
  event.preventDefault();
  mapInteractionActive = true;
  const oldZoom = Number(map.dataset.mapZoom);
  const multiplier = Math.exp(-event.deltaY * 0.0015);
  const nextZoom = clampZoom(oldZoom * multiplier);
  if (Math.abs(nextZoom - oldZoom) < 0.001) {
    mapInteractionActive = false;
    scheduleLiveRefresh();
    return;
  }
  const bounds = viewport.getBoundingClientRect();
  const pointerX = event.clientX - bounds.left;
  const pointerY = event.clientY - bounds.top;
  const contentX = viewport.scrollLeft + pointerX;
  const contentY = viewport.scrollTop + pointerY;
  const ratio = nextZoom / oldZoom;
  map.dataset.mapZoom = String(nextZoom);
  map.style.width = `${Number(map.dataset.baseWidth) * nextZoom}px`;
  const output =
    viewport.closest("section")?.querySelector("[data-map-zoom-output]") ||
    document.querySelector("[data-map-zoom-output]");
  if (output) output.textContent = `${Math.round(nextZoom * 100)}%`;
  requestAnimationFrame(() => {
    viewport.scrollLeft = contentX * ratio - pointerX;
    viewport.scrollTop = contentY * ratio - pointerY;
  });
  synchronizeZoom(nextZoom, viewport, map);
}

function setupMap(viewport) {
  if (viewport.dataset.mapEnhanced === "true") return;
  viewport.dataset.mapEnhanced = "true";
  const initialMap = viewport.querySelector("[data-map-zoom]");
  if (viewport.dataset.restoredScroll === "true") {
    delete viewport.dataset.restoredScroll;
  } else if (initialMap instanceof SVGElement) {
    requestAnimationFrame(() => centerFollowedCharacter(viewport, initialMap));
  }
  let startX = 0;
  let startY = 0;
  let startLeft = 0;
  let startTop = 0;
  let dragged = false;

  viewport.addEventListener("pointerdown", (event) => {
    if (
      event.button !== 0 ||
      event.target.closest("a") ||
      !viewport.querySelector("[data-map-zoom]")
    ) return;
    startX = event.clientX;
    startY = event.clientY;
    startLeft = viewport.scrollLeft;
    startTop = viewport.scrollTop;
    dragged = false;
    mapInteractionActive = true;
    viewport.setPointerCapture(event.pointerId);
    viewport.classList.add("is-panning");
  });

  viewport.addEventListener("pointermove", (event) => {
    if (!viewport.hasPointerCapture(event.pointerId)) return;
    const deltaX = event.clientX - startX;
    const deltaY = event.clientY - startY;
    if (Math.abs(deltaX) + Math.abs(deltaY) > 4) dragged = true;
    if (dragged) {
      event.preventDefault();
      viewport.scrollLeft = startLeft - deltaX;
      viewport.scrollTop = startTop - deltaY;
    }
  });

  const finishPan = (event) => {
    if (!viewport.hasPointerCapture(event.pointerId)) return;
    viewport.releasePointerCapture(event.pointerId);
    viewport.classList.remove("is-panning");
    if (!dragged) {
      mapInteractionActive = false;
      scheduleLiveRefresh();
      return;
    }
    const map = viewport.querySelector("[data-map-zoom]");
    if (map instanceof SVGElement) {
      synchronizeZoom(Number(map.dataset.mapZoom), viewport, map);
    } else {
      mapInteractionActive = false;
      scheduleLiveRefresh();
    }
  };
  viewport.addEventListener("pointerup", finishPan);
  viewport.addEventListener("pointercancel", finishPan);
  viewport.addEventListener(
    "click",
    (event) => {
      if (dragged) {
        event.preventDefault();
        event.stopPropagation();
        dragged = false;
      }
    },
    true,
  );
  viewport.addEventListener(
    "wheel",
    (event) => applyWheelZoom(viewport, event),
    {passive: false},
  );
}

function setupMaps() {
  const viewports = document.querySelectorAll(
    "[data-map-viewport], #world-render",
  );
  for (const viewport of viewports) setupMap(viewport);
}

document.addEventListener(
  "click",
  (event) => {
    const submitter = event.target.closest(
      "button[type=submit], input[type=submit]",
    );
    if (submitter?.form?.matches("form[data-ui-targets]")) {
      activatedSubmitters.set(submitter.form, submitter);
    }
  },
  true,
);

document.addEventListener("submit", (event) => {
  const form = event.target.closest("form[data-ui-targets]");
  if (!form) return;
  event.preventDefault();
  const submitter = event.submitter || activatedSubmitters.get(form);
  activatedSubmitters.delete(form);
  updateFromForm(form, submitter).catch((error) => {
    console.error("Partial form update failed", error);
    window.location.assign(window.location.href);
  });
});

document.addEventListener("click", (event) => {
  const link = event.target.closest("a[data-ui-link-targets]");
  if (link && event.button === 0 && !event.ctrlKey && !event.metaKey && !event.shiftKey) {
    event.preventDefault();
    updateFromLink(link).catch((error) => {
      console.error("Partial navigation failed", error);
      window.location.assign(link.getAttribute("href"));
    });
    return;
  }

  const button = event.target.closest("[data-copy-target]");
  if (!button) return;
  const target = document.querySelector(button.dataset.copyTarget);
  if (!target) return;
  navigator.clipboard.writeText(target.textContent).then(
    () => {
      button.textContent = "Copied";
    },
    (error) => {
      button.textContent = "Copy failed";
      console.error("Clipboard write failed", error);
    },
  );
});

window.addEventListener("popstate", () => {
  actionInFlight = true;
  fetchPage(window.location.href)
    .then((stateDocument) => replaceTargets(pageTargetSelectors(), stateDocument))
    .catch((error) => {
      console.error("History navigation update failed", error);
      window.location.reload();
    })
    .finally(() => {
      actionInFlight = false;
      scheduleLiveRefresh();
    });
});

document.addEventListener("visibilitychange", scheduleLiveRefresh);
document.body.dataset.uiEnhanced = "true";
setupMaps();
scheduleLiveRefresh();
