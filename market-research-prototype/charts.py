"""
Inline SVG chart renderers — no external chart libraries needed for our small datasets.
SVGs embed directly in HTML reports without any JS.
"""
from __future__ import annotations
import html as html_mod


def competitor_map_svg(clustering: dict, whitespace: dict | None = None,
                        width: int = 880, height: int = 600) -> str:
    """
    Render a 2D PCA scatter map of competitors with cluster colors.
    Highlights the whitespace cell if find_whitespace() found one.

    Input:
      clustering: output of clustering.cluster_competitors()
      whitespace: output of clustering.find_whitespace()

    Output: SVG string ready to embed in HTML.
    """
    if not clustering or clustering.get("error"):
        return f'<div style="color:#9ca3af;font-size:11pt">No competitor map (need ≥4 brands)</div>'

    coords = clustering.get("coordinates", {})
    clusters = clustering.get("clusters", [])
    if not coords:
        return '<div style="color:#9ca3af">No competitor coordinates</div>'

    # Compute bounds
    xs = [c[0] for c in coords.values()]
    ys = [c[1] for c in coords.values()]
    if not xs:
        return '<div style="color:#9ca3af">No data</div>'

    margin = 78  # roomier gutters so axis + corner labels never clip
    plot_w = width - 2 * margin
    plot_h = height - 2 * margin

    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    x_range = max(x_max - x_min, 0.01)
    y_range = max(y_max - y_min, 0.01)

    # Pad ranges by 10% so points aren't on the edge
    x_min -= x_range * 0.1
    x_max += x_range * 0.1
    y_min -= y_range * 0.1
    y_max += y_range * 0.1
    x_range = x_max - x_min
    y_range = y_max - y_min

    def to_px(x, y):
        px = margin + (x - x_min) / x_range * plot_w
        py = margin + (1 - (y - y_min) / y_range) * plot_h  # flip Y
        return px, py

    # Build cluster → color map
    palette = ["#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ef4444", "#06b6d4", "#ec4899", "#84cc16"]
    cluster_color = {}
    brand_to_cluster = {}
    for c in clusters:
        cid = c["id"]
        cluster_color[cid] = palette[cid % len(palette)]
        for member in c.get("members", []):
            brand_to_cluster[member] = cid

    # Start SVG
    # Responsive: scales to the container width (up to native size) instead of a fixed 600px.
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
           f'preserveAspectRatio="xMidYMid meet" '
           f'style="width:100%;max-width:{width}px;height:auto;display:block;margin:0 auto;'
           f'background:#fff;border:1px solid #e5e7eb;border-radius:8px">']

    # Background
    svg.append(f'<rect x="{margin}" y="{margin}" width="{plot_w}" height="{plot_h}" fill="#f9fafb"/>')

    # Whitespace overlay
    if whitespace and whitespace.get("whitespace_found"):
        gap = whitespace.get("largest_gap_location") or [0, 0]
        # Each grid cell is plot_w/4 wide
        cw = plot_w / 4
        ch = plot_h / 4
        # Grid is row=y (inverted), col=x
        gx = margin + gap[1] * cw
        gy = margin + (3 - gap[0]) * ch
        svg.append(
            f'<rect x="{gx}" y="{gy}" width="{cw}" height="{ch}" '
            f'fill="rgba(245,158,11,0.15)" stroke="#f59e0b" stroke-width="1.5" stroke-dasharray="4 4"/>'
        )
        # Whitespace label
        lx = gx + cw / 2
        ly = gy + ch / 2
        svg.append(
            f'<text x="{lx}" y="{ly}" text-anchor="middle" fill="#b45309" '
            f'font-size="11" font-weight="600">whitespace</text>'
        )

    # Iter 42 (issue 8): proper 4-quadrant positioning map with crosshair axes
    # going through the centroid + per-quadrant labels at the corners.
    axis_labels = clustering.get("axis_labels") or {}
    pc1_meta = axis_labels.get("pc1") or {}
    pc2_meta = axis_labels.get("pc2") or {}
    pc1_label = pc1_meta.get("label") or "PC1 (largest variance)"
    pc2_label = pc2_meta.get("label") or "PC2"
    pc1_high = pc1_meta.get("high_meaning") or "high"
    pc1_low = pc1_meta.get("low_meaning") or "low"
    pc2_high = pc2_meta.get("high_meaning") or "high"
    pc2_low = pc2_meta.get("low_meaning") or "low"

    cx_axis = (margin + (width - margin)) // 2  # x-axis center (vertical line)
    cy_axis = (margin + (height - margin)) // 2  # y-axis center (horizontal line)

    # Crosshair (the 4-quadrant divider)
    svg.append(f'<line x1="{cx_axis}" y1="{margin}" x2="{cx_axis}" y2="{height - margin}" '
               f'stroke="#cbd5e1" stroke-width="1" stroke-dasharray="3,3"/>')
    svg.append(f'<line x1="{margin}" y1="{cy_axis}" x2="{width - margin}" y2="{cy_axis}" '
               f'stroke="#cbd5e1" stroke-width="1" stroke-dasharray="3,3"/>')

    # cycle25 (issue 5): use the SHORT axis labels (the topic), not the long
    # meaning sentence. Previously corner labels truncated the meaning string
    # mid-word; now they pair the topic words "↗ High {pc1.label} · High {pc2.label}"
    # which is informative and fits.
    def _short(s, n=22):
        s = (s or "").strip()
        return s if len(s) <= n else s[:n-1] + "…"
    pc1_short = _short(pc1_label)
    pc2_short = _short(pc2_label)
    # top-right
    svg.append(f'<text x="{width - margin - 4}" y="{margin + 12}" text-anchor="end" font-size="11" fill="#94a3b8" font-style="italic">↗ High {html_mod.escape(pc1_short)} · High {html_mod.escape(pc2_short)}</text>')
    # top-left
    svg.append(f'<text x="{margin + 4}" y="{margin + 12}" text-anchor="start" font-size="11" fill="#94a3b8" font-style="italic">↖ Low {html_mod.escape(pc1_short)} · High {html_mod.escape(pc2_short)}</text>')
    # bottom-right
    svg.append(f'<text x="{width - margin - 4}" y="{height - margin - 4}" text-anchor="end" font-size="11" fill="#94a3b8" font-style="italic">↘ High {html_mod.escape(pc1_short)} · Low {html_mod.escape(pc2_short)}</text>')
    # bottom-left
    svg.append(f'<text x="{margin + 4}" y="{height - margin - 4}" text-anchor="start" font-size="11" fill="#94a3b8" font-style="italic">↙ Low {html_mod.escape(pc1_short)} · Low {html_mod.escape(pc2_short)}</text>')

    # Main axis labels (centered on each axis)
    svg.append(f'<text x="{width//2}" y="{height - 16}" text-anchor="middle" fill="#475569" font-size="13" font-weight="600">{html_mod.escape(pc1_label)} →</text>')
    svg.append(f'<text x="22" y="{height//2}" text-anchor="middle" fill="#475569" font-size="13" font-weight="600" transform="rotate(-90 22 {height//2})">{html_mod.escape(pc2_label)} →</text>')

    # Title
    pct_var = clustering.get("pca_explained_variance", 0)
    svg.append(f'<text x="{margin}" y="{margin - 24}" fill="#1f2937" font-size="14" font-weight="600">Competitive landscape — {clustering.get("k","?")} clusters · {int(pct_var*100)}% variance explained</text>')

    # Plot points
    for brand, (x, y) in coords.items():
        px, py = to_px(x, y)
        cid = brand_to_cluster.get(brand, 0)
        color = cluster_color.get(cid, "#6b7280")
        # Circle
        svg.append(f'<circle cx="{px}" cy="{py}" r="8" fill="{color}" stroke="#fff" stroke-width="2"/>')
        # Brand label
        safe_brand = html_mod.escape(brand)
        svg.append(f'<text x="{px + 12}" y="{py + 4}" fill="#1f2937" font-size="11" font-weight="500">{safe_brand}</text>')

    # Legend (if multiple clusters)
    if len(clusters) > 1:
        legend_y = height - 5
        legend_x = margin
        svg.append(f'<g font-size="9" fill="#4b5563">')
        for i, c in enumerate(clusters):
            color = cluster_color.get(c["id"], "#6b7280")
            cx = legend_x + i * 80
            svg.append(f'<circle cx="{cx}" cy="{legend_y - 4}" r="4" fill="{color}"/>')
            svg.append(f'<text x="{cx + 8}" y="{legend_y - 1}">cluster {c["id"]} ({c["size"]})</text>')
        svg.append('</g>')

    svg.append('</svg>')
    return "\n".join(svg)


def segment_radar_svg(segment: dict, width: int = 360, height: int = 360) -> str:
    """
    Iter 39: render a 5-axis radar chart of a single segment's scores.
    `segment` is one entry from segment_ranking.top_5 — must have a `scores`
    dict with the 5 metrics. Each metric value is 0-1.

    Returns an inline SVG string (no JS, prints cleanly to PDF).
    """
    import math
    scores = segment.get("scores") or {}
    if not scores:
        return ""
    METRICS = [
        ("wtp_x_market_size",   "WTP × Size"),
        ("low_price_elasticity","Low elasticity"),
        ("low_competition",     "Low competition"),
        ("ease_of_reach",       "Ease of reach"),
        ("growth_potential",    "Growth potential"),
    ]
    cx, cy = width / 2, height / 2
    radius = min(width, height) * 0.35

    def _val(key):
        e = scores.get(key)
        if isinstance(e, dict):
            try: return float(e.get("score", 0))
            except (ValueError, TypeError): return 0
        try: return float(e or 0)
        except (ValueError, TypeError): return 0

    # 5 axis points around a circle, starting at top (-π/2)
    n = len(METRICS)
    pts = []
    for i, (k, label) in enumerate(METRICS):
        angle = -math.pi / 2 + 2 * math.pi * i / n
        v = max(0, min(1, _val(k)))
        x = cx + radius * v * math.cos(angle)
        y = cy + radius * v * math.sin(angle)
        pts.append((x, y, angle, label, v))

    # Build SVG
    parts = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">']
    # Concentric grid rings (0.25, 0.5, 0.75, 1.0)
    for ring in (0.25, 0.5, 0.75, 1.0):
        ring_pts = []
        for i in range(n):
            a = -math.pi / 2 + 2 * math.pi * i / n
            ring_pts.append(f"{cx + radius * ring * math.cos(a):.1f},{cy + radius * ring * math.sin(a):.1f}")
        parts.append(f'<polygon points="{" ".join(ring_pts)}" fill="none" stroke="#e5e7eb" stroke-width="1"/>')
    # Axis spokes
    for i in range(n):
        a = -math.pi / 2 + 2 * math.pi * i / n
        x2 = cx + radius * math.cos(a)
        y2 = cy + radius * math.sin(a)
        parts.append(f'<line x1="{cx}" y1="{cy}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#e5e7eb" stroke-width="1"/>')
    # Filled value polygon
    poly_pts = " ".join(f"{x:.1f},{y:.1f}" for x, y, *_ in pts)
    parts.append(f'<polygon points="{poly_pts}" fill="rgba(59,130,246,0.18)" stroke="#3b82f6" stroke-width="2"/>')
    # Value dots
    for x, y, _, _, v in pts:
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#3b82f6"/>')
    # Axis labels
    for x, y, a, label, v in pts:
        # Place labels just outside the max ring
        lx = cx + (radius + 22) * math.cos(a)
        ly = cy + (radius + 22) * math.sin(a) + 4
        anchor = "middle"
        if math.cos(a) > 0.3: anchor = "start"
        elif math.cos(a) < -0.3: anchor = "end"
        parts.append(f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" font-size="10" fill="#1f2937" font-weight="600">{html_mod.escape(label)}</text>')
        parts.append(f'<text x="{lx:.1f}" y="{ly + 12:.1f}" text-anchor="{anchor}" font-size="9" fill="#6b7280">{v:.2f}</text>')
    parts.append('</svg>')
    return "\n".join(parts)


def signal_bar_svg(label: str, value: float, max_value: float = 100,
                   width: int = 300, color: str = "#3b82f6") -> str:
    """Render a single horizontal bar — useful for inline metric viz."""
    pct = max(0, min(100, (value / max_value) * 100))
    return f'''<svg width="{width}" height="20" xmlns="http://www.w3.org/2000/svg">
      <rect x="0" y="6" width="{width}" height="8" rx="4" fill="#e5e7eb"/>
      <rect x="0" y="6" width="{width * pct / 100}" height="8" rx="4" fill="{color}"/>
      <text x="0" y="-2" font-size="10" fill="#4b5563">{html_mod.escape(label)}</text>
      <text x="{width}" y="-2" text-anchor="end" font-size="10" fill="#1f2937" font-weight="600">{value}</text>
    </svg>'''
