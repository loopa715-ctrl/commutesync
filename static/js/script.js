// CommuteSync JavaScript
document.addEventListener("DOMContentLoaded", function () {
    // Smooth scrolling for in-page links
    document.querySelectorAll('a[href^="#"]').forEach(function (link) {
        link.addEventListener("click", function (event) {
            const target = document.querySelector(this.getAttribute("href"));
            if (target) {
                event.preventDefault();
                target.scrollIntoView({ behavior: "smooth" });
            }
        });
    });

    // Confirm before destructive actions (delete ride, cancel booking)
    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
        form.addEventListener("submit", function (event) {
            if (!confirm(form.dataset.confirm)) {
                event.preventDefault();
            }
        });
    });

    // Prevent double-submitting forms
    document.querySelectorAll("form").forEach(function (form) {
        form.addEventListener("submit", function (event) {
            if (event.defaultPrevented) return;
            const btn = form.querySelector('button[type="submit"]');
            if (btn) setTimeout(function () { btn.disabled = true; }, 0);
        });
    });

    // Auto-hide flash messages
    setTimeout(function () {
        document.querySelectorAll(".flash-success").forEach(function (el) {
            el.style.display = "none";
        });
    }, 4000);

    initSos();
    initRouteSelection();
});

// ---------------------------------------------------------------- Safety SOS
// Opens the phone's own SMS app with a pre-filled message to the user's
// emergency contact. Nothing is sent automatically - the person still has
// to press send in their messaging app. No backend or third-party SMS
// service is involved.
function initSos() {
    const btn = document.getElementById("sos-btn");
    if (!btn) return;

    btn.addEventListener("click", function () {
        const phone = btn.dataset.phone;
        if (!phone) {
            window.location.href = "/safety?need_contact=1";
            return;
        }

        const name = btn.dataset.name || "A CommuteSync user";
        let message = "SOS: " + name + " needs help.";

        if (btn.dataset.ridePickup) {
            message += " Ride: " + btn.dataset.ridePickup + " to " +
                btn.dataset.rideDestination + " on " + btn.dataset.rideDate +
                " at " + btn.dataset.rideTime + ".";
        }
        message += " Sent via CommuteSync.";

        const send = function (extra) {
            const isIOS = /iPhone|iPad|iPod/i.test(navigator.userAgent);
            const separator = isIOS ? "&" : "?";
            const body = encodeURIComponent(message + (extra || ""));
            window.location.href = "sms:" + phone + separator + "body=" + body;
        };

        if (navigator.geolocation) {
            // getCurrentPosition's own `timeout` option isn't reliably honored by
            // every browser (e.g. if the location-permission prompt is never
            // answered), which can leave SOS looking stuck. This fallback timer
            // guarantees the SMS opens within 4s regardless of what geolocation does.
            let settled = false;
            const settle = function (extra) {
                if (settled) return;
                settled = true;
                send(extra);
            };
            const fallback = setTimeout(function () { settle(""); }, 4000);

            navigator.geolocation.getCurrentPosition(
                function (position) {
                    clearTimeout(fallback);
                    const mapsLink = " My location: https://maps.google.com/?q=" +
                        position.coords.latitude + "," + position.coords.longitude;
                    settle(mapsLink);
                },
                function () {
                    clearTimeout(fallback);
                    settle("");
                },
                { timeout: 3000 }
            );
        } else {
            send("");
        }
    });
}

// ---------------------------------------------------------------- Route selection
// On the Find Ride page, clicking a ride card selects it and shows its
// route in a sticky summary bar, with a Join button wired to that card's
// own join form.
function initRouteSelection() {
    const cards = document.querySelectorAll(".ride-card[data-ride-id]");
    const summary = document.getElementById("route-summary");
    if (!cards.length || !summary) return;

    const summaryText = document.getElementById("route-summary-text");
    const summaryJoin = document.getElementById("route-summary-join");

    cards.forEach(function (card) {
        card.setAttribute("tabindex", "0");
        card.setAttribute("role", "button");

        const select = function () {
            cards.forEach(function (c) { c.classList.remove("selected"); });
            card.classList.add("selected");

            summaryText.textContent = card.dataset.pickup + " → " +
                card.dataset.destination + " · " + card.dataset.date +
                " " + card.dataset.time + " · ₹" + card.dataset.fare;
            summary.hidden = false;

            const joinForm = document.getElementById("join-form-" + card.dataset.rideId);
            if (joinForm) {
                summaryJoin.hidden = false;
                summaryJoin.onclick = function (event) {
                    event.preventDefault();
                    joinForm.requestSubmit();
                };
            } else {
                summaryJoin.hidden = true;
            }

            summary.scrollIntoView({ behavior: "smooth", block: "nearest" });
        };

        card.addEventListener("click", function (event) {
            if (event.target.closest("form, button, a")) return;
            select();
        });
        card.addEventListener("keydown", function (event) {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                select();
            }
        });
    });
}
