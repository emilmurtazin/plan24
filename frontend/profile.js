/* Профиль: имя, фамилия, e-mail (с подтверждением адреса), смена пароля. */
'use strict';

SCREENS.profile = async v => {
  const me = S.me;
  v.innerHTML = `<h1>Профиль</h1>
    ${me.email_verified ? '' : `<div class="banner warn" id="vb"><span><b>Почта не подтверждена.</b> Мы отправили письмо со ссылкой на ${esc(me.email)}. Без подтверждения не работает восстановление пароля.</span><button class="small" id="vr">Отправить письмо ещё раз</button></div>`}
    <div class="grid2">
    <form class="card" id="pf" novalidate><h2>Данные</h2>
      <dl class="kv"><dt>Организация</dt><dd>${esc(me.tenant.name)}</dd><dt>Роль</dt><dd>${esc(ROLE_RU[me.role])}</dd><dt>Почта</dt><dd>${me.email_verified ? '<span class="badge b-done">подтверждена</span>' : '<span class="badge b-risk">не подтверждена</span>'}</dd></dl>
      <div class="row2"><label class="f">Имя<input name="first_name" value="${esc(me.first_name)}" required autocomplete="given-name"></label>
      <label class="f">Фамилия<input name="last_name" value="${esc(me.last_name)}" required autocomplete="family-name"></label></div>
      <label class="f">E-mail<input name="email" type="email" value="${esc(me.email)}" required autocomplete="email"></label>
      <label class="f" id="cpw" hidden>Текущий пароль (нужен для смены e-mail)<input name="current_password" type="password" autocomplete="current-password"></label>
      <div class="err" data-err role="alert"></div><button type="submit">Сохранить</button></form>
    <form class="card" id="pw" novalidate><h2>Смена пароля</h2>
      <label class="f">Текущий пароль<input name="current_password" type="password" required autocomplete="current-password"></label>
      <label class="f">Новый пароль<input name="new_password" type="password" required minlength="8" autocomplete="new-password"><span>Не короче 8 символов</span></label>
      <label class="f">Повторите новый пароль<input name="again" type="password" required autocomplete="new-password"></label>
      <div class="err" data-err role="alert"></div><button type="submit">Сменить пароль</button></form></div>`;
  const pf = $('#pf'), pw = $('#pw');
  pf.elements.email.oninput = () => { $('#cpw').hidden = pf.elements.email.value.trim().toLowerCase() === me.email; };
  if ($('#vr')) $('#vr').onclick = async () => { try { await POST('/auth/resend-verification'); toast('Письмо отправлено'); } catch (e) { toast(errText(e), true); } };
  pf.onsubmit = async e => {
    e.preventDefault(); const er = $('[data-err]', pf); er.textContent = '';
    if (!pf.checkValidity()) { pf.reportValidity(); return; }
    const f = pf.elements, body = { first_name: f.first_name.value.trim(), last_name: f.last_name.value.trim() };
    if (f.email.value.trim().toLowerCase() !== me.email) { body.email = f.email.value.trim(); body.current_password = f.current_password.value; }
    try { const u = await api('PATCH', '/auth/profile', body); Object.assign(S.me, u); toast('Профиль сохранён'); start(); show('profile'); }
    catch (x) { er.textContent = errText(x); }
  };
  pw.onsubmit = async e => {
    e.preventDefault(); const er = $('[data-err]', pw); er.textContent = '';
    if (!pw.checkValidity()) { pw.reportValidity(); return; }
    const f = pw.elements;
    if (f.new_password.value !== f.again.value) { er.textContent = 'Новый пароль и повтор не совпадают'; return; }
    try {
      await POST('/auth/change-password', { current_password: f.current_password.value, new_password: f.new_password.value });
      /* смена пароля завершает все сессии — входим заново с новым паролем */
      const r = await POST('/auth/login', { email: S.me.email, password: f.new_password.value }); setTokens(r);
      pw.reset(); toast('Пароль изменён');
    } catch (x) { er.textContent = errText(x); }
  };
};
