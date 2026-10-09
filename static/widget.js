/**
 * MTS Call Agent — embeddable chat widget.
 * Drop into any business site:
 *
 *   <script>
 *     window.MTSCallAgentConfig = {
 *       apiBase: "https://api.mts-call-agent.example.com", // пусто = тот же origin
 *       businessName: "ИП Иванов",
 *       callerPhone: "web-widget"
 *     };
 *   </script>
 *   <script src=".../static/widget.js"></script>
 *
 * Расширяемость: после загрузки доступен window.MTSCallAgentWidget
 * с методами open(), close(), sendMessage(text) и полем config —
 * вешай свои обработчики/фичи поверх этого, не трогая файл.
 */
(function () {
  "use strict";

  var cfg = Object.assign(
    {
      apiBase: "",
      businessName: "MTS Call Agent",
      accent: "#E30611",
      callerPhone: "web-widget",
      greeting: "Здравствуйте! Чем помочь?",
    },
    window.MTSCallAgentConfig || {}
  );

  var sessionId =
    "web-" + Math.random().toString(36).slice(2) + Date.now().toString(36);

  function injectStyles() {
    var css =
      ".mtsca-btn{position:fixed;right:24px;bottom:24px;width:60px;height:60px;border-radius:999px;background:" +
      cfg.accent +
      ";border:none;cursor:pointer;box-shadow:0 8px 24px rgba(0,0,0,.18);display:flex;align-items:center;justify-content:center;z-index:999998;}" +
      ".mtsca-btn svg{width:26px;height:26px;}" +
      ".mtsca-panel{position:fixed;right:24px;bottom:96px;width:360px;max-width:calc(100vw - 32px);height:480px;max-height:calc(100vh - 140px);background:#FFFFFF;border:1px solid #ECEAE4;border-radius:20px;box-shadow:0 16px 48px rgba(0,0,0,.18);display:none;flex-direction:column;overflow:hidden;font-family:'Golos Text',ui-sans-serif,system-ui,sans-serif;z-index:999999;}" +
      ".mtsca-panel.mtsca-open{display:flex;}" +
      ".mtsca-head{padding:16px 18px;border-bottom:1px solid #ECEAE4;font-size:15px;font-weight:600;color:#17171A;display:flex;justify-content:space-between;align-items:center;}" +
      ".mtsca-close{cursor:pointer;background:none;border:none;font-size:18px;color:#86847C;line-height:1;}" +
      ".mtsca-msgs{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px;}" +
      ".mtsca-msg{max-width:80%;padding:10px 14px;border-radius:14px;font-size:14px;line-height:1.5;}" +
      ".mtsca-msg.user{align-self:flex-end;background:" +
      cfg.accent +
      ";color:#FAFAF9;border-radius:14px 14px 2px 14px;}" +
      ".mtsca-msg.agent{align-self:flex-start;background:#F6F5F0;color:#17171A;border-radius:14px 14px 14px 2px;}" +
      ".mtsca-msg.system{align-self:center;background:#F1EFE8;color:#55554F;font-size:12px;border-radius:999px;padding:6px 14px;}" +
      ".mtsca-inputrow{display:flex;gap:8px;padding:12px;border-top:1px solid #ECEAE4;}" +
      ".mtsca-input{flex:1;border:1px solid #ECEAE4;border-radius:999px;padding:10px 16px;font-size:14px;outline:none;font-family:inherit;}" +
      ".mtsca-send{width:40px;height:40px;border-radius:999px;background:" +
      cfg.accent +
      ";border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0;}" +
      ".mtsca-send svg{width:16px;height:16px;}";
    var style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);
  }

  function el(tag, cls) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    return e;
  }

  function escapeHtml(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function addMessage(container, role, text) {
    var msg = el("div", "mtsca-msg " + role);
    msg.innerHTML = escapeHtml(text);
    container.appendChild(msg);
    container.scrollTop = container.scrollHeight;
  }

  function sendMessage(text, msgsEl) {
    addMessage(msgsEl, "user", text);
    fetch(cfg.apiBase + "/api/hermes/dialog", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        caller_phone: cfg.callerPhone,
        user_message: text,
      }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (data) {
        addMessage(msgsEl, "agent", data.agent_response || "");
        if (data.transfer) {
          addMessage(
            msgsEl,
            "system",
            "Переведено: " + (data.transfer.department || "специалист")
          );
        }
      })
      .catch(function () {
        addMessage(msgsEl, "system", "Ошибка связи с агентом. Попробуйте ещё раз.");
      });
  }

  function build() {
    injectStyles();

    var btn = el("button", "mtsca-btn");
    btn.setAttribute("aria-label", "Открыть чат с " + cfg.businessName);
    btn.innerHTML =
      '<svg viewBox="0 0 24 24" fill="none"><path d="M6.6 10.8c1.4 2.8 3.8 5.1 6.6 6.6l2.2-2.2c.3-.3.7-.4 1-.2 1.1.4 2.3.6 3.6.6.6 0 1 .4 1 1v3.4c0 .6-.4 1-1 1C10.3 21 3 13.7 3 4.9c0-.6.4-1 1-1h3.4c.6 0 1 .4 1 1 0 1.3.2 2.5.6 3.6.1.4 0 .8-.2 1l-2.2 2.3z" stroke="#FAFAF9" stroke-width="1.6" stroke-linejoin="round"/></svg>';

    var panel = el("div", "mtsca-panel");

    var head = el("div", "mtsca-head");
    var title = el("div");
    title.textContent = cfg.businessName;
    var closeBtn = el("button", "mtsca-close");
    closeBtn.setAttribute("aria-label", "Закрыть чат");
    closeBtn.textContent = "✕";
    head.appendChild(title);
    head.appendChild(closeBtn);

    var msgs = el("div", "mtsca-msgs");
    addMessage(msgs, "agent", cfg.greeting);

    var inputRow = el("div", "mtsca-inputrow");
    var input = document.createElement("input");
    input.className = "mtsca-input";
    input.type = "text";
    input.placeholder = "Напишите сообщение…";
    var sendBtn = el("button", "mtsca-send");
    sendBtn.setAttribute("aria-label", "Отправить сообщение");
    sendBtn.innerHTML =
      '<svg viewBox="0 0 24 24" fill="none"><path d="M4 12h16M13 5l7 7-7 7" stroke="#FAFAF9" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    inputRow.appendChild(input);
    inputRow.appendChild(sendBtn);

    panel.appendChild(head);
    panel.appendChild(msgs);
    panel.appendChild(inputRow);

    document.body.appendChild(panel);
    document.body.appendChild(btn);

    function toggle() {
      panel.classList.toggle("mtsca-open");
      if (panel.classList.contains("mtsca-open")) input.focus();
    }

    btn.addEventListener("click", toggle);
    closeBtn.addEventListener("click", toggle);

    function submit() {
      var text = input.value.trim();
      if (!text) return;
      input.value = "";
      sendMessage(text, msgs);
    }

    sendBtn.addEventListener("click", submit);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") submit();
    });

    window.MTSCallAgentWidget = {
      open: function () {
        panel.classList.add("mtsca-open");
        input.focus();
      },
      close: function () {
        panel.classList.remove("mtsca-open");
      },
      sendMessage: function (text) {
        sendMessage(text, msgs);
      },
      config: cfg,
    };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})();
