from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _dashboard_html():
    return (ROOT / "frontend" / "dashboard.html").read_text(encoding="utf-8")


def test_dashboard_version_check_uses_github_api_before_raw_cdn_fallback():
    api_url = "https://api.github.com/repos/ariaprism/ombrebrain/contents/VERSION?ref=main"
    raw_url = "https://raw.githubusercontent.com/ariaprism/ombrebrain/main/VERSION?t="

    for rel_path in ("frontend/dashboard.html",):
        html = (ROOT / rel_path).read_text(encoding="utf-8")

        assert api_url in html
        assert raw_url in html
        assert html.index(api_url) < html.index(raw_url)


def test_dashboard_hot_update_surfaces_csrf_proxy_guidance():
    html = (ROOT / "frontend" / "dashboard.html").read_text(encoding="utf-8")
    block = html[html.index("window.doHotUpdate = async function()") :]
    block = block[: block.index("window.checkGitHubVersion = async function()")]

    assert "fetch(BASE + '/api/do-update'" in block
    assert "authFetch(BASE + '/api/do-update'" not in block
    assert "热更新不是可重试写操作" in block
    assert "failure.error === 'Cross-origin request rejected'" in block
    assert "这不是 CORS 缺失" in block
    assert "OMBRE_TRUSTED_PROXY_CIDRS" in block


def test_dashboard_keeps_faq_content_without_an_author_help_entry():
    html = (ROOT / "frontend" / "dashboard.html").read_text(encoding="utf-8")
    faq_view = html[html.index('id="faq-view"') : html.index('<!-- Logs Tab View')]
    logs_view = html[html.index('id="logs-view"') : html.index('<!-- Settings Tab View -->')]

    faq_url = "https://docs.qq.com/doc/DRHp6UW9oYmd3QW5Z"
    assert 'data-tab="faq"' not in html
    assert '<span class="tab-en">FAQ</span>' not in html
    assert 'id="faq-section"' in faq_view
    assert faq_url in faq_view
    assert 'target="_blank"' in faq_view
    assert 'rel="noopener noreferrer"' in faq_view
    assert 'id="faq-section"' not in logs_view
    assert "getElementById('faq-view').style.display = target === 'faq'" in html


def test_dashboard_hides_author_entries_and_keeps_mobile_editor_inside_panel():
    html = (ROOT / "frontend" / "dashboard.html").read_text(encoding="utf-8")

    assert 'onclick="openFeedback()" title="给作者反馈' not in html
    assert 'data-tab="about"' not in html
    assert 'class="bucket-edit-grid"' in html
    assert 'grid-template-columns: 76px minmax(0, 1fr) !important;' in html
    assert '.bucket-edit-grid .edit-content-label,' in html
    assert 'grid-column: 1 / -1;' in html
    assert 'min-height: 240px !important;' in html


def test_dashboard_navigation_lives_behind_the_ob_door():
    html = (ROOT / "frontend" / "dashboard.html").read_text(encoding="utf-8")

    assert 'class="ob-door"' in html
    assert 'id="nav-drawer"' in html
    assert 'id="nav-drawer-overlay" onclick="closeNavDrawer()"' in html
    assert '<div class="tab active" data-tab="list"><span>记忆</span>' in html
    assert '<div class="tab" data-tab="breath"><span>浮现</span>' in html
    assert '<div class="tab" data-tab="network"><span>暗线</span>' in html
    assert 'onclick="closeNavDrawer();openSelfPanel()"' in html
    assert '<span>我记得的</span><span class="tab-en">我 · 她 · 他们</span>' in html
    assert '<button class="self-fab"' not in html
    assert "activateDashboardTab(tab);\n    closeNavDrawer();" in html


def test_dashboard_room_theme_and_memory_only_search():
    html = _dashboard_html()

    list_start = html.index('<div class="content" id="list-view">')
    filters_start = html.index('<div class="filters" id="filters">', list_start)
    search_start = html.index('<div class="search-bar">', list_start)
    assert list_start < search_start < filters_start
    assert html.count('id="search-input"') == 1
    assert 'id="theme-toggle"' in html
    assert "localStorage.setItem('ombreTheme', normalized)" in html
    assert "meta.content = normalized === 'night' ? '#171411' : '#e7e2d8'" in html
    assert 'class="config-section settings-session-actions"' in html


def test_dashboard_network_supports_pan_zoom_and_reset():
    html = _dashboard_html()

    assert "canvas.addEventListener('wheel'" in html
    assert "canvas.addEventListener('pointerdown'" in html
    assert "canvas.addEventListener('pointermove'" in html
    assert 'function zoomNetworkAt(' in html
    assert 'function resetNetworkView()' in html
    assert 'touch-action: none' in html
    assert html.index('<canvas id="network-canvas"></canvas>') < html.index('<div class="network-legend">')
    assert "canvas.insertAdjacentElement('afterend', bar);" in html


def test_dashboard_local_preview_hides_embedding_noise_and_uses_room_language():
    html = _dashboard_html()

    assert 'function isLocalDashboard()' in html
    assert "!(isLocalDashboard() && c.id === 'embedding')" in html
    assert "!isLocalDashboard() && !d.embedding_enabled" in html
    assert "total + ' 记忆 · ' + pinned + ' 珍藏" in html
    assert "decaying ? '记忆在呼吸' : '呼吸暂停'" in html
    assert '#sec-version { display: none !important; }' not in html
    assert 'class="bucket-bulkbar"' not in html
    assert 'class="bucket-select"' not in html
    assert html.index('<div id="status-banner"') > html.index('id="settings-view"')
    assert 'border-style: dashed !important;' in html
