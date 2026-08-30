export async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {"Content-Type": "application/json", ...(options.headers ?? {})},
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") {
        detail = body.detail;
      } else if (Array.isArray(body.detail)) {
        detail = body.detail
          .map((issue) => {
            const location = Array.isArray(issue.loc)
              ? issue.loc.filter((part) => part !== "body").join(".")
              : "";
            return `${location ? `${location}: ` : ""}${issue.msg ?? "Invalid value"}`;
          })
          .join("; ");
      }
    } catch {
      // The HTTP status is sufficient when the body is not JSON.
    }
    throw new Error(detail);
  }
  return response.json();
}
