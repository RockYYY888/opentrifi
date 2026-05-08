/// <reference types="node" />

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("global layout styles", () => {
	it("reserves a stable page scrollbar gutter to prevent centered layout shifts", () => {
		const globalStylesheet = readFileSync(resolve(process.cwd(), "src/index.css"), "utf8");

		expect(globalStylesheet).toMatch(
			/html\s*\{[\s\S]*scrollbar-gutter:\s*stable(?:\s+both-edges)?\s*;/,
		);
		expect(globalStylesheet).toMatch(
			/body\s*\{[\s\S]*scrollbar-gutter:\s*stable(?:\s+both-edges)?\s*;/,
		);
	});

	it("keeps key inner scroll containers width-stable when overflow toggles", () => {
		const globalStylesheet = readFileSync(resolve(process.cwd(), "src/index.css"), "utf8");
		const assetStylesheet = readFileSync(
			resolve(process.cwd(), "src/components/assets/asset-components.css"),
			"utf8",
		);

		expect(globalStylesheet).toMatch(
			/\.admin-feedback-list\s*\{[\s\S]*overflow-y:\s*auto\s*;[\s\S]*scrollbar-gutter:\s*stable\s*;/,
		);
		expect(assetStylesheet).toMatch(
			/\.asset-manager__search-list\s*\{[\s\S]*overflow-y:\s*auto\s*;[\s\S]*scrollbar-gutter:\s*stable\s*;/,
		);
	});

	it("locks the mobile page width while keeping table overflow local", () => {
		const globalStylesheet = readFileSync(resolve(process.cwd(), "src/index.css"), "utf8");

		expect(globalStylesheet).toMatch(
			/html\s*\{[\s\S]*width:\s*100%\s*;[\s\S]*max-width:\s*100%\s*;[\s\S]*overscroll-behavior-x:\s*none\s*;/,
		);
		expect(globalStylesheet).toMatch(
			/body\s*\{[\s\S]*width:\s*100%\s*;[\s\S]*max-width:\s*100%\s*;[\s\S]*overflow-x:\s*clip\s*;[\s\S]*overscroll-behavior-x:\s*none\s*;/,
		);
		expect(globalStylesheet).toMatch(
			/#root\s*\{[\s\S]*width:\s*100%\s*;[\s\S]*max-width:\s*100%\s*;[\s\S]*overflow-x:\s*clip\s*;/,
		);
		expect(globalStylesheet).toMatch(
			/\.app-shell\s*\{[\s\S]*width:\s*100%\s*;[\s\S]*min-width:\s*0\s*;[\s\S]*overflow-x:\s*clip\s*;/,
		);
		expect(globalStylesheet).toMatch(
			/\.table-scroll\s*\{[\s\S]*max-width:\s*100%\s*;[\s\S]*overflow-x:\s*auto\s*;[\s\S]*overscroll-behavior-x:\s*contain\s*;/,
		);
	});

	it("keeps modal and viewport rules robust on mobile and keyboard navigation", () => {
		const globalStylesheet = readFileSync(resolve(process.cwd(), "src/index.css"), "utf8");
		const assetStylesheet = readFileSync(
			resolve(process.cwd(), "src/components/assets/asset-components.css"),
			"utf8",
		);
		const analyticsStylesheet = readFileSync(
			resolve(process.cwd(), "src/components/analytics/analytics.css"),
			"utf8",
		);

		expect(globalStylesheet).toContain("min-height: 100dvh;");
		expect(globalStylesheet).not.toContain(".session-recovery-mask");
		expect(globalStylesheet).toMatch(
			/\.workspace-switch__button:focus-visible\s*\{[\s\S]*outline:\s*2px\s+solid/,
		);
		expect(globalStylesheet).toMatch(
			/\.workspace-switch__button\s*\{[\s\S]*border:\s*1\.5px\s+solid\s+rgba\(255,\s*255,\s*255,\s*0\.24\)\s*;[\s\S]*box-shadow:\s*[\s\S]*0\s+0\s+0\s+1\.5px\s+rgba\(255,\s*255,\s*255,\s*0\.08\)\s*;/,
		);
		expect(globalStylesheet).toMatch(
			/\.workspace-switch__button:hover\s*\{[\s\S]*border-color:\s*rgba\(255,\s*255,\s*255,\s*0\.4\)\s*;/,
		);
		expect(globalStylesheet).toMatch(
			/\.feedback-modal__panel\s*\{[\s\S]*max-height:\s*min\(84dvh,\s*720px\)\s*;[\s\S]*overflow-y:\s*auto\s*;/,
		);
		expect(globalStylesheet).toMatch(
			/\.feedback-modal\s*\{[\s\S]*overflow:\s*hidden\s*;[\s\S]*overscroll-behavior:\s*none\s*;/,
		);
		expect(globalStylesheet).toMatch(
			/\.feedback-modal__panel--list-layout\s*\{[\s\S]*display:\s*flex\s*;[\s\S]*overflow:\s*hidden\s*;/,
		);
		expect(globalStylesheet).toMatch(
			/\.feedback-modal__head-actions\s*\{[\s\S]*display:\s*flex\s*;/,
		);
		expect(globalStylesheet).toMatch(
			/\.admin-feedback-list\s*\{[\s\S]*overflow-y:\s*auto\s*;[\s\S]*overscroll-behavior:\s*contain\s*;/,
		);
		expect(globalStylesheet).toMatch(
			/\.admin-feedback-card__head\s*\{[\s\S]*display:\s*grid\s*;[\s\S]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s+auto\s*;/,
		);
		expect(globalStylesheet).toMatch(
			/\.feedback-modal__backdrop\s*\{[\s\S]*border-radius:\s*0\s*;[\s\S]*box-shadow:\s*none\s*;/,
		);
		expect(assetStylesheet).toMatch(
			/\.asset-manager__modal-panel\s*\{[\s\S]*max-height:\s*min\(84dvh,\s*760px\)\s*;[\s\S]*overflow-y:\s*auto\s*;/,
		);
		expect(assetStylesheet).toMatch(
			/\.feedback-modal__panel\.agent-workspace__modal-panel\s*\{[\s\S]*max-height:\s*min\(84dvh,\s*760px\)\s*;[\s\S]*overflow:\s*hidden\s*;/,
		);
		expect(assetStylesheet).toMatch(
			/\.feedback-modal__panel\.asset-records__modal-panel\s*\{[\s\S]*max-height:\s*min\(84dvh,\s*720px\)\s*;[\s\S]*overflow-y:\s*hidden\s*;/,
		);
		expect(assetStylesheet).toMatch(
			/\.asset-manager__modal-backdrop\s*\{[\s\S]*border-radius:\s*0\s*;[\s\S]*box-shadow:\s*none\s*;/,
		);
		expect(assetStylesheet).toMatch(
			/\.asset-manager__modal-backdrop:hover:not\(:disabled\)\s*\{[\s\S]*transform:\s*none\s*;/,
		);
		const feedbackModalPanelBlock =
			globalStylesheet.match(/\.feedback-modal__panel\s*\{[\s\S]*?\n\}/)?.[0] ?? "";
		const assetModalPanelBlock =
			assetStylesheet.match(/\.asset-manager__modal-panel\s*\{[\s\S]*?\n\}/)?.[0] ?? "";
		expect(globalStylesheet).not.toContain("radial-gradient(");
		expect(globalStylesheet).not.toMatch(/\.ambient(?:-left|-right)?\s*\{/);
		expect(assetStylesheet).not.toContain("radial-gradient(");
		expect(analyticsStylesheet).not.toContain("radial-gradient(");
		expect(feedbackModalPanelBlock).not.toContain("radial-gradient(");
		expect(assetModalPanelBlock).not.toContain("radial-gradient(");
		expect(globalStylesheet).toContain("env(safe-area-inset-top)");
		expect(globalStylesheet).toContain("env(safe-area-inset-right)");
		expect(globalStylesheet).toContain("env(safe-area-inset-bottom)");
		expect(globalStylesheet).toContain("env(safe-area-inset-left)");
		expect(assetStylesheet).toMatch(
			/@media \(max-width:\s*720px\)\s*\{[\s\S]*\.feedback-modal__panel\.agent-workspace__modal-panel,[\s\S]*\.feedback-modal__panel\.asset-records__modal-panel,[\s\S]*\.asset-manager__modal-panel\s*\{[\s\S]*max-height:\s*100%\s*;/,
		);
		expect(assetStylesheet).toMatch(
			/@media \(max-width:\s*720px\)\s*\{[\s\S]*\.agent-workspace__scroll-region,[\s\S]*\.asset-records__scroll-region\s*\{[\s\S]*min-height:\s*0\s*;[\s\S]*overflow:\s*visible\s*;/,
		);
		expect(analyticsStylesheet).toMatch(
			/\.analytics-segmented button:focus-visible\s*\{[\s\S]*outline:\s*2px\s+solid/,
		);
	});

	it("preserves semantic hidden behavior even when layout classes set display styles", () => {
		const globalStylesheet = readFileSync(resolve(process.cwd(), "src/index.css"), "utf8");

		expect(globalStylesheet).toMatch(
			/\[hidden\]\s*\{[\s\S]*display:\s*none\s*!important\s*;/,
		);
	});
});
