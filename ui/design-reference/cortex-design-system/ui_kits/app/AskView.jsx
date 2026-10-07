function Composer({ onAsk, autoFocus }) {
  const { Icon, IconButton } = window.Cortex;
  const [q, setQ] = React.useState('');
  const send = () => { if (q.trim()) { onAsk(q.trim()); setQ(''); } };
  return (
    <div style={{ background: 'var(--white)', border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius-lg)', padding: '14px 14px 10px 18px', display: 'flex', flexDirection: 'column', gap: 10, boxShadow: 'var(--shadow-1)' }}>
      <textarea autoFocus={autoFocus} rows={2} value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}
        placeholder="Ask anything about Company A…" style={{ border: 0, outline: 0, resize: 'none', font: '400 16px/1.45 var(--font-sans)', color: 'var(--text-strong)', background: 'transparent' }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <IconButton icon="paperclip" label="Attach" size="sm" />
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, height: 28, padding: '0 10px', borderRadius: 999, background: 'var(--bg-app)', font: '400 12px/1 var(--font-sans)', color: 'var(--text-body)' }}><Icon name="layers" size={13} />All sources</span>
        <div style={{ flex: 1 }} />
        <button onClick={send} aria-label="Send" style={{ width: 32, height: 32, borderRadius: '50%', border: 0, background: q.trim() ? 'var(--orange)' : 'var(--gray-200)', color: 'var(--ink)', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', transition: 'background var(--dur-fast)' }}><Icon name="arrow-up" size={16} strokeWidth={2.5} /></button>
      </div>
    </div>
  );
}
function Answer({ q, viewAs }) {
  const { Avatar, SourcePill, Badge, Icon, Card, Button } = window.Cortex;
  const guest = PEOPLE.find(p => p.id === viewAs).access === 'Guest';
  const a = guest ? ANSWER.guest : ANSWER.full;
  const [ready, setReady] = React.useState(false);
  React.useEffect(() => { const t = setTimeout(() => setReady(true), 700); return () => clearTimeout(t); }, []);
  const me = PEOPLE.find(p => p.id === viewAs);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', justifyContent: 'flex-end' }}>
        <div style={{ background: 'var(--white)', border: '1px solid var(--border-subtle)', borderRadius: '16px 16px 4px 16px', padding: '10px 14px', font: '400 15px/1.45 var(--font-sans)', maxWidth: '75%' }}>{q}</div>
        <Avatar src={me.src} name={me.name} size={28} />
      </div>
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        <span style={{ width: 28, height: 28, flexShrink: 0, borderRadius: '50%', background: '#fff', boxShadow: 'inset 0 0 0 2px var(--orange)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><img src={ASSET + 'logo/cortex-mark.svg'} style={{ width: 18 }} /></span>
        {!ready ? <div style={{ font: '400 14px/28px var(--font-sans)', color: 'var(--text-muted)' }}>Reading {guest ? '1 source' : '3 sources'}…</div> :
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 14, minWidth: 0 }}>
          {a.text.map((para, i) => <p key={i} style={{ margin: 0, font: '400 15px/1.6 var(--font-sans)', color: 'var(--text-strong)', textWrap: 'pretty' }}>
            {para.map((s, j) => typeof s === 'number' ? <SourcePill key={j} index={s} label={(a.sources[s - 1] || {}).label || 'Restricted'} size="sm" variant="soft" style={{ verticalAlign: 'middle', margin: '0 2px' }} /> : j === 1 ? <b key={j}>{s}</b> : s)}
          </p>)}
          {a.restricted && <Card tone="sunken" padding={14} radius="md"><div style={{ display: 'flex', gap: 10, alignItems: 'center', font: '400 13px/1.4 var(--font-sans)', color: 'var(--text-body)' }}><Icon name="lock" size={16} /><span style={{ flex: 1 }}>{a.restricted} sources hidden by your access level. Cortex never shows what you couldn’t open yourself.</span><Button size="sm" variant="secondary">Request access</Button></div></Card>}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 4 }}>
            <div style={{ font: 'var(--type-eyebrow)', letterSpacing: 'var(--ls-eyebrow)', textTransform: 'uppercase', color: 'var(--text-muted)' }}>Sources</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(200px,1fr))', gap: 8 }}>
              {a.sources.map((s, i) => { const p = PEOPLE.find(x => x.id === s.who); return (
                <Card key={i} interactive padding={12} radius="md"><div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}><span style={{ minWidth: 14, height: 14, borderRadius: 4, background: 'var(--orange)', font: '700 10px/14px var(--font-mono)', textAlign: 'center' }}>{i + 1}</span><span style={{ font: '600 12px/1 var(--font-mono)' }}>{s.label}</span></div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, font: '400 12px/1 var(--font-sans)', color: 'var(--text-muted)' }}><Avatar src={p.src} name={p.name} size={20} />{p.name} · {s.tool}</div>
                </div></Card>); })}
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, color: 'var(--text-muted)' }}>
            {['copy', 'thumbs-up', 'thumbs-down', 'share'].map(ic => <window.Cortex.IconButton key={ic} icon={ic} label={ic} size="sm" />)}
          </div>
        </div>}
      </div>
    </div>
  );
}
function AskView({ thread, setThread, viewAs }) {
  const { SourcePill } = window.Cortex;
  const me = PEOPLE.find(p => p.id === viewAs);
  if (!thread) return (
    <div style={{ maxWidth: 'var(--content-max)', margin: '0 auto', padding: '14vh 32px 32px', display: 'flex', flexDirection: 'column', gap: 28 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div style={{ font: '400 44px/1.04 var(--font-sans)', letterSpacing: 'var(--ls-display)' }}>Morning, {me.name}.</div>
        <div style={{ font: '700 44px/1.04 var(--font-sans)', letterSpacing: 'var(--ls-display)' }}>What do you need to know?</div>
      </div>
      <Composer onAsk={setThread} autoFocus />
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        {SUGGESTIONS.map(s => <button key={s} onClick={() => setThread(s)} style={{ display: 'inline-flex', alignItems: 'center', gap: 8, height: 32, padding: '0 14px', borderRadius: 999, border: '1px solid var(--border-subtle)', background: 'var(--white)', font: '400 13px/1 var(--font-sans)', color: 'var(--text-body)', cursor: 'pointer', whiteSpace: 'nowrap', textAlign: 'left' }}><window.Cortex.Icon name="corner-down-right" size={14} color="var(--orange)" />{s}</button>)}
      </div>
    </div>
  );
  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ flex: 1, overflow: 'auto' }}><div style={{ maxWidth: 'var(--content-max)', margin: '0 auto', padding: '40px 32px' }}><Answer key={thread + viewAs} q={thread} viewAs={viewAs} /></div></div>
      <div style={{ maxWidth: 'var(--content-max)', width: '100%', margin: '0 auto', padding: '0 32px 24px', boxSizing: 'border-box' }}><Composer onAsk={setThread} /></div>
    </div>
  );
}
