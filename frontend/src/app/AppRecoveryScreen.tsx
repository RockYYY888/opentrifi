export function AppRecoveryScreen() {
	return (
		<div className="app-shell">
			<header className="hero-panel">
				<div className="hero-copy-block">
					<div className="hero-copy-block__main">
						<p className="eyebrow">SESSION RESTORE</p>
						<h1>正在恢复登录状态</h1>
						<p className="hero-copy">确认当前会话之前，不展示本地缓存里的资产数据。</p>
						<p className="hero-subtle">验证通过后会继续回到你的工作区。</p>
					</div>
				</div>

				<div className="summary-grid" aria-label="恢复中的资产概览">
					{["总资产", "现金资产", "投资类", "固定资产", "其他", "负债"].map((label) => (
						<div key={label} className="stat-card neutral">
							<span>{label}</span>
							<strong>—</strong>
						</div>
					))}
				</div>
			</header>
		</div>
	);
}
