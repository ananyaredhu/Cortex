function Sidebar({ view, setView, viewAs, setViewAs, onNewThread }) {
  const { Logo, Icon, Avatar, Menu } = window.Cortex;
  const [menu, setMenu] = React.useState(false);
  const me = PEOPLE.find(p => p.id === viewAs);
  const Nav = ({ id, icon, label, count }) => {
    const on = view === id;
    return (
      <div onClick={() => setView(id)} style={{ display: 'flex', alignItems: 'center', gap: 10, height: 34, padding: '0 10px', borderRadius: 8, cursor: 'pointer',
        background: on ? 'var(--white)' : 'transparent', boxShadow: on ? 'var(--shadow-1)' : 'none', font: (on ? '700' : '400') + ' 14px/1 var(--font-sans)', color: 'var(--text-strong)' }}>
        <Icon name={icon} size={16} color={on ? 'var(--ink)' : 'var(--text-body)'} />
        <span style={{ flex: 1 }}>{label}</span>
        {count && <span style={{ font: '600 11px/1 var(--font-mono)', color: 'var(--text-muted)' }}>{count}</span>}
      </div>
    );
  };
  return (
    <aside style={{ width: 'var(--sidebar-w)', flexShrink: 0, height: '100%', boxSizing: 'border-box', padding: '18px 12px', display: 'flex', flexDirection: 'column', gap: 18, background: 'var(--paper-2)' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 6px' }}>
        <Logo size={20} markSrc={ASSET + 'logo/cortex-mark.svg'} />
        <span style={{ font: '400 12px/1 var(--font-sans)', color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>Company A</span>
      </div>
      <div onClick={onNewThread} style={{ display: 'flex', alignItems: 'center', gap: 8, height: 36, padding: '0 12px', borderRadius: 'var(--radius-sm)', background: 'var(--ink)', color: 'var(--paper)', font: '700 14px/1 var(--font-sans)', cursor: 'pointer' }}>
        <Icon name="sparkles" size={16} color="var(--orange)" /><span style={{ flex: 1 }}>Ask Cortex</span><span style={{ font: '500 11px/1 var(--font-mono)', color: 'var(--gray-400)' }}>⌘K</span>
      </div>
      <nav style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <Nav id="ask" icon="message-square" label="Threads" />
        <Nav id="sources" icon="plug" label="Sources" count="6" />
        <Nav id="people" icon="users" label="People & access" count="5" />
      </nav>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <div style={{ padding: '0 10px 6px', font: 'var(--type-eyebrow)', letterSpacing: 'var(--ls-eyebrow)', textTransform: 'uppercase', color: 'var(--text-muted)' }}>Recent</div>
        {THREADS.map(t => <div key={t} onClick={() => setView('ask')} style={{ padding: '8px 10px', borderRadius: 8, font: '400 13px/1.2 var(--font-sans)', color: 'var(--text-body)', cursor: 'pointer', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{t}</div>)}
      </div>
      <div style={{ flex: 1 }} />
      <div style={{ position: 'relative' }}>
        {menu && <Menu style={{ position: 'absolute', bottom: 'calc(100% + 6px)', left: 0 }} width={224}
          sections={[{ title: 'View Cortex as', items: PEOPLE.map(p => ({ label: p.name + ' · ' + p.role, avatar: p.src, checked: p.id === viewAs, id: p.id })) }]}
          onSelect={it => { setViewAs(it.id); setMenu(false); }} />}
        <div onClick={() => setMenu(!menu)} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: 8, borderRadius: 'var(--radius-md)', background: 'var(--white)', cursor: 'pointer', border: '1px solid var(--border-subtle)' }}>
          <Avatar src={me.src} name={me.name} size={32} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ font: '700 13px/1.2 var(--font-sans)' }}>{me.name}</div>
            <div style={{ font: '400 12px/1.2 var(--font-sans)', color: 'var(--text-muted)' }}>{me.role}</div>
          </div>
          <Icon name="chevrons-up-down" size={14} color="var(--text-muted)" />
        </div>
      </div>
    </aside>
  );
}
