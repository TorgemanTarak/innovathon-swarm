"""Rendered UI tests backed by isolated real app/agent/coordinator processes.

Happy-path HTTP responses are never mocked. API reads and filesystem assertions
supplement browser clicks to verify ordering and rollback. Error tests explicitly
inject network failures or malformed responses at the browser boundary.
"""

import json

from conftest import TOKEN, Fleet, connect
from playwright.sync_api import Page, expect


def card(page: Page, name: str):
    return page.locator(".agent-card").filter(
        has=page.get_by_role("heading", name=name, exact=True)
    )


def reset(page: Page) -> None:
    page.locator("#reset-button").click()
    expect(page.locator("#metric-healthy")).to_have_text("4")
    expect(page.locator("#metric-recipes")).to_have_text("0")
    expect(page.locator("#metric-replays")).to_have_text("0")


def next_poll(page: Page) -> None:
    with page.expect_response(lambda r: r.url.endswith("/api/state") and r.status == 200):
        pass


def test_manual_repair_canary_and_compatible_fleet_recover(page: Page, fleet: Fleet) -> None:
    connect(page, fleet)
    # Open the actual app from the rendered endpoint link and exercise /work through its UI.
    with page.expect_popup() as popup:
        card(page, "Mac A").get_by_role("link", name="Open app").click()
    app = popup.value
    app.locator("#auto").uncheck()
    app.locator("#load").click()
    expect(app.locator("#status")).to_have_attribute("data-state", "healthy")
    page.locator("#pause-button").click()
    expect(page.locator("#pause-label")).to_have_text("Resume rollout")
    page.locator("#fault-button").click()
    expect(page.locator("#metric-incidents")).to_have_text("4")
    fleet.wait(lambda s: all(a["incident_id"] for a in s["agents"]))
    app.locator("#load").click()
    expect(app.locator("#status")).to_have_attribute("data-state", "error")

    # A genuine manual originating file rename, not the coordinator's repair simulation.
    cache = fleet.root / "mac-a/cache/workspace.json"
    cache.rename(cache.with_name("workspace.manually-quarantined.json"))
    expect(page.locator(".recipe .status-pill").first).to_have_text("candidate")
    expect(page.locator("#metric-healthy")).to_have_text("1")
    expect(page.locator("#metric-replays")).to_have_text("0")
    next_poll(page)
    assert fleet.client.get("/api/state").json()["outcomes"] == []
    page.locator("#pause-button").click()
    expect(page.locator("#network-status")).to_have_text("CANARY IN PROGRESS")
    expect(page.locator("#metric-replays")).to_have_text("2")
    expect(page.locator("#metric-healthy")).to_have_text("3")
    expect(card(page, "Mac D").locator(".status-pill")).to_have_text("Issue detected")
    expect(page.locator(".recipe .status-pill").first).to_have_text("validated")
    page.locator(".recipe details summary").click()
    expect(page.locator(".attempt")).to_have_count(2)
    expect(page.locator(".attempt .status-pill")).to_have_text(["verified", "verified"])
    app.locator("#load").click()
    expect(app.locator("#status")).to_have_attribute("data-state", "healthy")

    state = fleet.wait(lambda s: any(e["kind"] == "repair_incompatible" for e in s["events"]))
    events = list(reversed(state["events"]))
    canary = next(e["agent_id"] for e in events if e["kind"] == "canary_started")
    verified = next(
        i
        for i, e in enumerate(events)
        if e["kind"] == "repair_verified" and e["agent_id"] == canary
    )
    follower = next(i for i, e in enumerate(events) if e["kind"] == "repair_dispatched")
    assert verified < follower
    assert {o["agent_id"] for o in state["outcomes"]} == {"mac-b", "mac-c"}
    assert all(o["checks_passed"] == o["checks_total"] == 3 for o in state["outcomes"])
    reset(page)
    app.close()


def test_failed_canary_rolls_back_and_reset_restores_fleet(page: Page, fleet: Fleet) -> None:
    connect(page, fleet)
    page.locator("#pause-button").click()
    expect(page.locator("#pause-label")).to_have_text("Resume rollout")
    page.locator("#fault-button").click()
    expect(page.locator("#metric-incidents")).to_have_text("4")
    fleet.wait(lambda s: all(a["incident_id"] for a in s["agents"]))
    page.locator("#source-select").select_option("mac-a")
    page.locator("#repair-button").click()
    expect(page.locator(".recipe .status-pill").first).to_have_text("candidate")
    page.locator(".failure-controls summary").click()
    for endpoint in ("mac-b", "mac-c"):
        page.locator("#failure-select").select_option(endpoint)
        page.locator("#failure-button").click()
        expect(page.locator("#failure-button")).to_be_enabled()
    fleet.wait(
        lambda s: (
            sum(
                e["kind"] == "demo_command_completed" and e["message"] == "fail_next_verification"
                for e in s["events"]
            )
            == 2
        )
    )
    page.locator("#pause-button").click()
    expect(page.locator("#network-status")).to_have_text("1 RECIPE PAUSED")
    page.locator(".recipe details summary").click()
    expect(page.locator(".attempt .status-pill")).to_have_text("failed")
    expect(page.locator(".attempt p")).to_contain_text("previous cache restored")
    expect(page.locator("#metric-replays")).to_have_text("0")
    state = fleet.client.get("/api/state").json()
    assert len(state["outcomes"]) == 1
    failed = state["outcomes"][0]["agent_id"]
    assert "CORRUPTED CACHE" in (fleet.root / failed / "cache/workspace.json").read_text()
    assert not any(e["kind"] == "repair_dispatched" for e in state["events"])
    # Global pause/resume must not reactivate a failed recipe.
    for label in ("Resume rollout", "Pause rollout"):
        page.locator("#pause-button").click()
        expect(page.locator("#pause-label")).to_have_text(label)
    next_poll(page)
    expect(page.locator("#network-status")).to_have_text("1 RECIPE PAUSED")
    expect(page.locator(".attempt")).to_have_count(1)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    reset(page)


def test_authentication_network_and_malformed_response_errors(page: Page, fleet: Fleet) -> None:
    page.goto(fleet.url)
    expect(page.locator("#access-dialog")).to_be_visible()
    expect(page.locator("#access-error")).to_contain_text("requires an access token")
    expect(page.locator("#fault-button")).to_be_disabled()
    page.locator("#close-dialog").click()
    page.locator("#retry-button").click()
    page.locator("#access-token").fill("incorrect-browser-test-token")
    page.locator("#access-form button[type=submit]").click()
    expect(page.locator("#access-error")).to_contain_text("not accepted")
    page.locator("#access-token").fill(TOKEN)
    page.locator("#access-form button[type=submit]").click()
    expect(page.locator("#metric-healthy")).to_have_text("4")
    page.reload()
    expect(page.locator("#connection")).to_have_class("connection live")
    expect(page.locator("#access-dialog")).not_to_be_visible()

    # Simulate transport loss, invalid JSON and invalid shape in a real browser.
    for body in (None, "<html>Proxy error</html>", json.dumps({"agents": []})):
        if body is None:
            page.route("**/api/state", lambda route: route.abort("failed"))
        else:
            page.route(
                "**/api/state",
                lambda route, _request, body=body: route.fulfill(
                    status=200, content_type="application/json", body=body
                ),
            )
        expect(page.locator("#connection")).to_have_class("connection offline")
        expect(page.locator("#error-banner")).to_be_visible()
        expect(page.locator("#fault-button")).to_be_disabled()
        expect(page.locator("#metric-healthy")).to_have_text("4")
        page.unroute("**/api/state")
        page.locator("#retry-button").click()
        expect(page.locator("#fault-button")).to_be_enabled()

    page.route(
        "**/api/demo/fault",
        lambda route: route.fulfill(
            status=503,
            content_type="application/json",
            body=json.dumps({"detail": "Temporary command rejection"}),
        ),
    )
    page.locator("#fault-button").click()
    expect(page.locator("#toast")).to_have_text("Temporary command rejection")
    expect(page.locator("#toast")).to_have_class("toast error")
    expect(page.locator("#fault-button")).to_be_enabled()
    page.unroute("**/api/demo/fault")
    # Hold polling errors too, so automatic reconnect cannot race the error assertion.
    page.route("**/api/state", lambda route: route.abort("failed"))
    page.route(
        "**/api/demo/fault",
        lambda route: route.fulfill(status=200, content_type="text/html", body="Invalid JSON"),
    )
    page.locator("#fault-button").click()
    expect(page.locator("#toast")).to_have_class("toast error")
    expect(page.locator("#fault-button")).to_be_disabled()
    expect(page.locator("#metric-healthy")).to_have_text("4")
    page.unroute("**/api/demo/fault")
    page.unroute("**/api/state")
    page.locator("#retry-button").click()
    expect(page.locator("#fault-button")).to_be_enabled()


def test_endpoint_links_keep_keyboard_focus_through_poll_and_updates(
    page: Page, fleet: Fleet
) -> None:
    connect(page, fleet)
    link = card(page, "Mac A").get_by_role("link", name="Open app")
    link.focus()
    next_poll(page)
    next_poll(page)
    expect(link).to_be_focused()
    # Another live dashboard changes real health while this tab keeps keyboard focus.
    other = page.context.new_page()
    connect(other, fleet)
    other.locator("#fault-button").click()
    expect(page.locator("#metric-incidents")).to_have_text("4")
    expect(link).to_be_focused()
    # Tab navigation must continue to the next endpoint rather than restarting at the page top.
    page.keyboard.press("Tab")
    expect(card(page, "Mac B").get_by_role("link", name="Open app")).to_be_focused()
    other.close()
