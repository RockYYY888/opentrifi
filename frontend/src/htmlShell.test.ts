import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

describe("HTML shell metadata", () => {
	it("declares explicit icon links for browsers and mobile launchers", () => {
		const html = readFileSync(resolve(process.cwd(), "index.html"), "utf-8");

		expect(html).toContain(
			'<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />',
		);
		expect(html).toContain('<link rel="icon" href="/pwa-icon.svg" type="image/svg+xml" />');
		expect(html).toContain('<link rel="apple-touch-icon" href="/pwa-icon.svg" />');
		expect(html).toContain('<link rel="manifest" href="/manifest.webmanifest" />');
	});
});
