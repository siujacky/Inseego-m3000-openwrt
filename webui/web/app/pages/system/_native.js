// Form values include hidden fields; passwordless networks must omit an empty key.
export function wifiCreateBody(values) {
  const body = Object.assign({}, values);
  if (body.password === '') delete body.password;
  return body;
}

export function hardwareSummary(data) {
  const fresh = data.fresh === true && Number.isFinite(data.age_s) && data.age_s >= 0 && data.age_s <= 30;
  const battery = data.battery || {}, display = data.display || {}, touch = data.touch || {};
  const voltage = fresh && Number.isFinite(battery.voltage_uv) && battery.voltage_uv > 0
    ? `${(battery.voltage_uv / 1e6).toFixed(3)} V last ADC voltage` : 'Voltage unavailable';
  const sampleAge = fresh && Number.isFinite(battery.sample_age_s) ? `sample age ${battery.sample_age_s} s` : 'sample age unknown';
  return {
    Report: fresh ? `Fresh producer observation (age ${data.age_s} s)` : `Hardware observation unavailable (${data.freshness || 'not current'})`,
    Battery: `${voltage}; ${sampleAge}; capacity and charging unavailable`,
    Display: fresh && display.registered === true ? 'Custom LCD driver registered; frame-write status is a software observation'
      : fresh && display.registered === false ? 'Custom LCD driver not registered' : 'LCD registration unavailable',
    Touch: fresh && touch.events_seen != null ? `${touch.events_seen} reported input events; ${(touch.event_devices || []).join(', ') || 'no device names reported'}` : 'Input event observation unavailable',
    'Physical validation': 'Visible image and physical touch remain unconfirmed by this telemetry.',
    'Modem processor': fresh && data.modem && data.modem.remoteproc_state || 'State unavailable',
    'IPA interfaces': fresh && data.ipa && data.ipa.interfaces && data.ipa.interfaces.length ? data.ipa.interfaces.join(', ') : 'Interface observation unavailable',
  };
}
