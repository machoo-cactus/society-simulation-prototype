export function renderTranscriptView({
  container,
  events,
  displayName,
  showDetail,
  optionalString,
}) {
  const entries = events.filter((event) =>
    /^(speech\.delivered|dialogue\.generated)$/.test(event.type)
  );
  container.replaceChildren();
  if (!entries.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No speech yet";
    container.append(empty);
    return;
  }
  for (const event of entries.slice(-100).reverse()) {
    const entry = document.createElement("article");
    entry.className = "transcript-entry";
    const recipients = Array.isArray(event.payload.recipient_ids)
      ? event.payload.recipient_ids.map(displayName).join(", ")
      : optionalString(event.payload.target_id)
        ? displayName(event.payload.target_id)
        : "unspecified";
    const meta = document.createElement("div");
    meta.className = "transcript-entry__meta";
    meta.textContent = `t${event.tick} · ${
      displayName(event.agentId ?? "unknown")
    } → ${recipients}${
      event.type === "dialogue.generated" ? " · legacy dialogue" : ""
    }`;
    const quote = document.createElement("blockquote");
    quote.textContent = String(event.payload.text ?? "");
    entry.append(meta, quote);
    entry.addEventListener("click", () => showDetail(event));
    container.append(entry);
  }
}
