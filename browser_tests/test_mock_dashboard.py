"""Real Chromium checks for the synthetic topology, separate from live repairs."""

from urllib.parse import urlparse

from conftest import TOKEN, Fleet
from playwright.sync_api import Page, expect


def test_mock_sizes_routes_and_live_fleet_isolation(page: Page, fleet: Fleet) -> None:
    before = fleet.client.get("/api/state").json()
    api_requests: list[str] = []

    def observe_request(request) -> None:
        if urlparse(request.url).path.startswith("/api/"):
            api_requests.append(request.url)

    page.on("request", observe_request)
    page.goto(f"{fleet.url}/mock")
    expect(page.locator(".mock-badge")).to_have_text("SYNTHETIC FLEET")
    expect(page.locator("#metric-nodes")).to_have_text("256")
    expect(page.locator("#metric-links")).to_have_text("3,456")
    expect(page.locator("#metric-contacts")).to_have_text("14")
    expect(page.locator("#metric-hops")).to_have_text("4")
    expect(page.locator("#route-steps li")).to_have_count(5)
    expect(page.locator("#fleet-graph")).to_have_attribute("data-route", "0101,3101,3301,3321,3320")

    for count, contacts, maximum, hops in [(64, "672", "11", "3"), (1024, "16,896", "17", "5")]:
        page.locator("#fleet-size").select_option(str(count))
        expect(page.locator("#fleet-graph")).to_have_attribute("data-node-count", str(count))
        expect(page.locator("#metric-nodes")).to_have_text(f"{count:,}")
        expect(page.locator("#metric-links")).to_have_text(contacts)
        expect(page.locator("#metric-contacts")).to_have_text(maximum)
        expect(page.locator("#metric-hops")).to_have_text(hops)
        expect(page.locator("#route-source option")).to_have_count(count)
        expect(page.locator("#route-target option")).to_have_count(count)

    expect(page.locator("#comparison-note")).to_contain_text("1,047,552")
    page.locator("#peer-links").uncheck()
    page.locator("#coordinator-links").uncheck()
    expect(page.locator("#metric-links")).to_have_text("16,896")
    page.locator("#reset-view").click()
    expect(page.locator("#peer-links")).to_be_checked()
    expect(page.locator("#coordinator-links")).to_be_checked()
    assert api_requests == [], "Synthetic graph must not read or mutate the live fleet API"

    after = fleet.client.get("/api/state").json()
    assert before["recipes"] == after["recipes"] == []
    assert before["outcomes"] == after["outcomes"] == []
    assert len(after["agents"]) == 4
    assert all(agent["health"] == "healthy" for agent in after["agents"])

    page.remove_listener("request", observe_request)
    page.get_by_role("link", name="Live dashboard", exact=True).click()
    expect(page).to_have_url(f"{fleet.url}/")
    expect(page.locator("#access-dialog")).to_be_visible()
    page.locator("#access-token").fill(TOKEN)
    page.locator("#access-form button[type=submit]").click()
    expect(page.locator("#metric-healthy")).to_have_text("4")
    for width in [320, 390]:
        page.set_viewport_size({"width": width, "height": 900})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        expect(page.get_by_role("link", name="Topology lab", exact=True)).to_be_in_viewport()
    page.get_by_role("link", name="Topology lab", exact=True).click()
    expect(page).to_have_url(f"{fleet.url}/mock")


def test_mock_trace_pause_reset_and_keyboard_inspection(page: Page, fleet: Fleet) -> None:
    page.goto(f"{fleet.url}/mock")
    page.locator("#trace-route").click()
    expect(page.locator("#pause-route")).to_be_enabled()
    page.locator("#pause-route").click()
    expect(page.locator("#pause-route")).to_have_text("Resume trace")
    expect(page.locator("#route-status")).to_contain_text("Paused synthetic route")
    page.locator("#peer-links").uncheck()
    expect(page.locator("#route-status")).to_contain_text("Paused synthetic route")
    page.locator("#pause-route").click()
    expect(page.locator("#route-status")).to_contain_text("Illustrated route complete")
    expect(page.locator("#pause-route")).to_be_disabled()

    last_step = page.locator("#route-steps button").last
    last_step.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#selected-id")).to_have_text("3320")
    page.locator("#use-source").click()
    expect(page.locator("#route-source")).to_have_value("248")
    expect(page.locator("#metric-hops")).to_have_text("0")
    expect(page.locator("#route-steps li")).to_have_count(1)
    page.locator("#trace-route").click()
    expect(page.locator("#pause-route")).to_be_disabled()

    page.locator("#route-target").select_option("9")
    page.locator("#route-steps button").last.click()
    expect(page.locator("#selected-status")).to_have_text("incompatible")
    expect(page.locator("#selected-status")).to_have_attribute("data-status", "incompatible")
    page.locator("#use-target").click()
    expect(page.locator("#route-target")).to_have_value("9")
    page.locator("#trace-route").click()
    page.locator("#pause-route").click()
    page.locator("#fleet-size").select_option("64")
    expect(page.locator("#pause-route")).to_be_disabled()
    expect(page.locator("#route-source")).to_have_value("17")
    expect(page.locator("#route-target")).to_have_value("56")
    page.locator("#reset-view").click()
    expect(page.locator("#peer-links")).to_be_checked()
    expect(page.locator("#metric-hops")).to_have_text("3")


def test_mock_mobile_and_reduced_motion(page: Page, fleet: Fleet) -> None:
    page.emulate_media(reduced_motion="reduce")
    page.set_viewport_size({"width": 320, "height": 800})
    page.goto(f"{fleet.url}/mock")
    for width in [320, 390, 768]:
        page.set_viewport_size({"width": width, "height": 900})
        page.locator("#fleet-size").select_option("1024")
        expect(page.locator("#metric-nodes")).to_have_text("1,024")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        box = page.locator("#fleet-graph").bounding_box()
        assert box is not None and box["width"] > 200 and box["height"] > 300
        page.locator("#trace-route").click()
        expect(page.locator("#pause-route")).to_be_disabled()
        expect(page.locator("#route-status")).to_contain_text("Synthetic route.")
        expect(page.locator("#route-steps li")).to_have_count(6)
    page.locator("#route-source").focus()
    page.keyboard.press("Home")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Tab")
    expect(page.locator("#route-source")).to_have_value("1")
    expect(page.locator("#route-target")).to_be_focused()
