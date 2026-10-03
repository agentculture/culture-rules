import type { Page } from "@playwright/test";

/**
 * The live feed (`GET /api/events/stream`, culture_rules/server/events.py)
 * for the mocked API. Each page gets a queue of SSE frames; every request
 * for the stream drains its queue and ends with `retry: 200`, so the
 * browser's EventSource reconnects shortly and picks up the next frames:
 * the same reconnect path a real server close takes.
 *
 * `broadcastWrites` makes every write a page sends (POST/PUT/DELETE under
 * /api) push a `change` frame to every live page, as the store's change feed
 * would. Register both after the page's API mock: Playwright runs the most
 * recently registered matching route first, and these fall back to it.
 */
export class LiveFeed {
  private queues = new Map<Page, string[]>();
  private seq = 0;
  readonly streams: string[] = [];

  async attach(page: Page) {
    this.queues.set(page, []);
    await page.route("**/api/events/stream**", async (route) => {
      this.streams.push(route.request().url());
      const frames = this.queues.get(page)!.splice(0);
      await route.fulfill({
        status: 200,
        headers: { "content-type": "text/event-stream", "cache-control": "no-cache" },
        body: `retry: 200\n\n${frames.join("")}`,
      });
    });
  }

  emit(collection: string, id: string, op = "update") {
    this.seq += 1;
    const cursor = JSON.stringify({ [collection]: `tok-${this.seq}` });
    const data = JSON.stringify({ collection, op, id, document: { id } });
    const frame = `event: change\nid: ${cursor}\ndata: ${data}\n\n`;
    for (const queue of this.queues.values()) queue.push(frame);
  }

  async broadcastWrites(page: Page) {
    await page.route("**/api/**", async (route) => {
      const request = route.request();
      if (request.method() !== "GET") {
        const path = new URL(request.url()).pathname.replace(/^\/api\//, "");
        const [collection, id = ""] = path.split("/");
        await route.fallback();
        this.emit(collection, id);
        return;
      }
      await route.fallback();
    });
  }
}
