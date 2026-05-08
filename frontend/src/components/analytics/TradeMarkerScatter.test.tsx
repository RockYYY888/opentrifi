import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TRADE_MARKER_POSITIVE_COLOR } from "./chartTradeMarkers";
import { TradeMarkerScatter } from "./TradeMarkerScatter";

describe("TradeMarkerScatter", () => {
	it("locks the overlay to the chart frame instead of stretching with the card", () => {
		const { container } = render(
			<TradeMarkerScatter
				markers={[
					{
						xValue: 10,
						yValue: 5,
						label: "B",
						dominantSide: "BUY",
						stroke: TRADE_MARKER_POSITIVE_COLOR,
						labelColor: TRADE_MARKER_POSITIVE_COLOR,
						fill: "rgba(8, 18, 34, 0.96)",
						events: [],
					},
				]}
				chartWidth={549}
				chartHeight={280}
				plotLeft={80}
				plotTop={18}
				plotWidth={441}
				plotHeight={216}
				xDomain={[0, 20]}
				yDomain={[0, 10]}
			/>,
		);

		const overlay = container.querySelector(
			".analytics-trade-marker-overlay",
		) as SVGElement | null;
		expect(overlay).not.toBeNull();
		expect(overlay?.getAttribute("style")).toContain("width: 549px");
		expect(overlay?.getAttribute("style")).toContain("height: 280px");
	});
});
