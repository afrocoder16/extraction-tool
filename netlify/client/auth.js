import {
  acceptInvite,
  getUser,
  handleAuthCallback,
  login,
  logout,
  requestPasswordRecovery,
  updateUser,
} from "@netlify/identity";

const style = document.createElement("style");
style.textContent = `
  .auth-cover { position:fixed; inset:0; z-index:10000; display:grid; place-items:center;
    padding:24px; background:#f4f5f7; color:#1d232a; font:14px/1.5 "Segoe UI",system-ui,sans-serif; }
  .auth-card { width:min(420px,100%); background:#fff; border:1px solid #dde1e8;
    border-radius:12px; box-shadow:0 16px 50px rgba(16,24,40,.14); padding:25px; }
  .auth-card h1 { margin:0 0 5px; font-size:22px; }
  .auth-card p { margin:0 0 18px; color:#6b7280; }
  .auth-card form { display:grid; gap:11px; }
  .auth-card label { display:grid; gap:4px; font-size:12px; color:#4b5563; }
  .auth-card input { width:100%; border:1px solid #cfd5df; border-radius:7px; padding:10px 11px; font:inherit; }
  .auth-card button { border:1px solid #8b1e2d; border-radius:7px; padding:9px 12px;
    background:#8b1e2d; color:#fff; cursor:pointer; font:inherit; }
  .auth-card button.link { border:0; background:transparent; color:#8b1e2d; padding:3px; }
  .auth-error { color:#9f1239 !important; min-height:20px; margin:0 !important; }
  .auth-chip { position:fixed; right:14px; bottom:14px; z-index:9000; display:flex; gap:8px;
    align-items:center; background:#fff; border:1px solid #dde1e8; border-radius:8px;
    box-shadow:0 2px 8px rgba(16,24,40,.12); padding:6px 8px 6px 10px; font:12px "Segoe UI",system-ui,sans-serif; }
  .auth-chip button { border:0; background:#f0f2f5; border-radius:5px; padding:5px 8px; cursor:pointer; }
`;
document.head.append(style);

function card(title, message, formHtml) {
  let cover = document.querySelector(".auth-cover");
  if (!cover) {
    cover = document.createElement("div");
    cover.className = "auth-cover";
    document.body.append(cover);
  }
  cover.innerHTML = `<section class="auth-card"><h1>${title}</h1><p>${message}</p>${formHtml}<p class="auth-error" id="authError"></p></section>`;
  return cover;
}

function message(error) {
  document.getElementById("authError").textContent = error?.message || String(error);
}

function showLogin() {
  const cover = card(
    "Wudase Mariam Review",
    "Sign in with the email address that received the Netlify invitation.",
    `<form id="loginForm">
      <label>Email<input name="email" type="email" autocomplete="email" required></label>
      <label>Password<input name="password" type="password" autocomplete="current-password" required></label>
      <button type="submit">Sign in</button>
      <button class="link" type="button" id="forgotPassword">Forgot password?</button>
    </form>`,
  );
  cover.querySelector("#loginForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector('[type="submit"]');
    button.disabled = true;
    try {
      const data = new FormData(event.currentTarget);
      await login(String(data.get("email")), String(data.get("password")));
      location.reload();
    } catch (error) {
      message(error);
      button.disabled = false;
    }
  });
  cover.querySelector("#forgotPassword").addEventListener("click", async () => {
    const email = cover.querySelector('[name="email"]').value.trim();
    if (!email) return message(new Error("Enter your email address first."));
    try {
      await requestPasswordRecovery(email);
      document.getElementById("authError").style.color = "#1f7a4d";
      document.getElementById("authError").textContent = "Password reset email sent.";
    } catch (error) {
      message(error);
    }
  });
}

function showPasswordForm(mode, token) {
  const invite = mode === "invite";
  const cover = card(
    invite ? "Accept your invitation" : "Choose a new password",
    invite ? "Create a password to open the review tool." : "Enter the new password you want to use.",
    `<form id="passwordForm">
      <label>New password<input name="password" type="password" autocomplete="new-password" minlength="8" required></label>
      <label>Confirm password<input name="confirm" type="password" autocomplete="new-password" minlength="8" required></label>
      <button type="submit">${invite ? "Accept invitation" : "Save new password"}</button>
    </form>`,
  );
  cover.querySelector("#passwordForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const password = String(data.get("password"));
    if (password !== data.get("confirm")) return message(new Error("Passwords do not match."));
    try {
      if (invite) await acceptInvite(token, password);
      else await updateUser({ password });
      history.replaceState(null, "", location.pathname + location.search);
      location.reload();
    } catch (error) {
      message(error);
    }
  });
}

function showUser(user) {
  document.querySelector(".auth-cover")?.remove();
  const chip = document.createElement("div");
  chip.className = "auth-chip";
  chip.innerHTML = `<span></span><button type="button">Sign out</button>`;
  chip.querySelector("span").textContent = user.email || user.name || "Signed in";
  chip.querySelector("button").addEventListener("click", async () => {
    await logout();
    location.reload();
  });
  document.body.append(chip);
}

async function start() {
  try {
    const callback = await handleAuthCallback();
    if (callback?.type === "invite") return showPasswordForm("invite", callback.token);
    if (callback?.type === "recovery") return showPasswordForm("recovery");
    const user = callback?.user || await getUser();
    if (user) showUser(user);
    else showLogin();
  } catch (error) {
    showLogin();
    message(error);
  }
}

start();
