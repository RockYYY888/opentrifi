import { useCallback, useEffect, useMemo, useState } from "react";
import type { HTMLAttributes } from "react";

import { useBodyScrollLock } from "../../hooks/useBodyScrollLock";

type ChartInteractionHandlers = Pick<
	HTMLAttributes<HTMLDivElement>,
	| "onPointerDown"
	| "onPointerMove"
	| "onPointerUp"
	| "onPointerCancel"
	| "onTouchStart"
	| "onTouchEnd"
	| "onTouchCancel"
>;

/**
 * Prevents the page from scrolling while the user is dragging inside a chart.
 * This keeps touch exploration focused on the chart instead of the page.
 */
export function useChartInteractionLock(): {
	chartInteractionHandlers: ChartInteractionHandlers;
	chartTooltipProps: { active?: boolean };
	isTouchInteracting: boolean;
} {
	const [isTouchInteracting, setIsTouchInteracting] = useState(false);
	const [lastPointerType, setLastPointerType] = useState<
		"mouse" | "touch" | null
	>(null);

	useBodyScrollLock(isTouchInteracting);

	const releaseInteractionLock = useCallback(() => {
		setIsTouchInteracting(false);
	}, []);

	const engageInteractionLock = useCallback(() => {
		setLastPointerType("touch");
		setIsTouchInteracting(true);
	}, []);

	useEffect(() => {
		if (!isTouchInteracting || typeof window === "undefined") {
			return;
		}

		window.addEventListener("pointerup", releaseInteractionLock);
		window.addEventListener("pointercancel", releaseInteractionLock);
		window.addEventListener("touchend", releaseInteractionLock);
		window.addEventListener("touchcancel", releaseInteractionLock);

		return () => {
			window.removeEventListener("pointerup", releaseInteractionLock);
			window.removeEventListener("pointercancel", releaseInteractionLock);
			window.removeEventListener("touchend", releaseInteractionLock);
			window.removeEventListener("touchcancel", releaseInteractionLock);
		};
	}, [isTouchInteracting, releaseInteractionLock]);

	const chartInteractionHandlers = useMemo<ChartInteractionHandlers>(
		() => ({
			onPointerDown: (event) => {
				if (event.pointerType === "mouse") {
					setLastPointerType("mouse");
					return;
				}

				engageInteractionLock();
			},
			onPointerMove: (event) => {
				if (event.pointerType === "mouse") {
					setLastPointerType("mouse");
				}
			},
			onPointerUp: releaseInteractionLock,
			onPointerCancel: releaseInteractionLock,
			onTouchStart: engageInteractionLock,
			onTouchEnd: releaseInteractionLock,
			onTouchCancel: releaseInteractionLock,
		}),
		[engageInteractionLock, releaseInteractionLock],
	);
	const chartTooltipProps = useMemo(
		() => (lastPointerType === "touch" ? { active: isTouchInteracting } : {}),
		[isTouchInteracting, lastPointerType],
	);

	return {
		chartInteractionHandlers,
		chartTooltipProps,
		isTouchInteracting,
	};
}
