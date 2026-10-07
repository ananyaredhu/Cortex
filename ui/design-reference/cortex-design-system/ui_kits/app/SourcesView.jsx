function PageHeader({ eyebrow, title, action }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div style={{ font: 'var(--type-eyebrow)', letterSpacing: 'var(--ls-eyebrow)', textTransform: 'uppercase', color: 'var(--text-muted)' }}>{eyebrow}</div>
        <h1 style={{ margin: 0, font: 'var(--type-h1)', letterSpacing: 'var(--ls-heading)' }}>{title}</h1>
      </div>
      {action}
    </div>
  );
}
function SourcesView({ toast }) {
  const { Button, Card, Icon, Switch, Badge, Tabs, Dialog, Input, Checkbox, IconButton } = window.Cortex;
  const [list, setList] = React.useState(SOURCES);
  const [dlg, setDlg] = React.useState(false);
  const [tab, setTab] = React.useState('all');
  const shown = list.filter(s => tab === 'all' || (tab === 'issues' ? s.status !== 'synced' : s.on));
  const badge = s => !s.on ? <Badge>Paused</Badge> : s.status === 'failed' ? <Badge tone="danger" dot>Sync failed</Badge> : <Badge tone="success" dot>Synced</Badge>;
  return (
    <div style={{ maxWidth: 960, margin: '0 auto', padding: '40px 32px', display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader eyebrow="Company A" title="Sources" action={<Button variant="accent" icon="plug" onClick={() => setDlg(true)}>Connect source</Button>} />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: 'all', label: 'All', count: list.length }, { value: 'active', label: 'Active', count: list.filter(s => s.on).length }, { value: 'issues', label: 'Needs attention', count: list.filter(s => s.status !== 'synced').length }]} />
      <Card padding={0}>
        {shown.map((s, i) => (
          <div key={s.id} style={{ display: 'grid', gridTemplateColumns: '40px minmax(0,1fr) 140px 120px 44px 36px', alignItems: 'center', gap: 14, padding: '14px 18px', borderTop: i ? '1px solid var(--border-subtle)' : 'none' }}>
            <span style={{ width: 40, height: 40, borderRadius: 'var(--radius-md)', background: 'var(--bg-app)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Icon name={s.icon} size={20} /></span>
            <div style={{ minWidth: 0 }}><div style={{ font: '700 15px/1.2 var(--font-sans)' }}>{s.name}</div><div style={{ font: '500 12px/1.4 var(--font-mono)', color: 'var(--text-muted)' }}>{s.items}</div></div>
            <div>{badge(s)}</div>
            <div style={{ font: '400 13px/1 var(--font-sans)', color: 'var(--text-muted)' }}>{s.on ? s.synced : '—'}</div>
            <Switch size="sm" checked={s.on} onChange={v => { setList(l => l.map(x => x.id === s.id ? { ...x, on: v, status: v ? (x.status === 'paused' ? 'synced' : x.status) : x.status } : x)); toast(v ? s.name + ' sync resumed' : s.name + ' paused'); }} />
            {s.status === 'failed' && s.on ? <IconButton icon="refresh-cw" label="Retry" size="sm" onClick={() => { setList(l => l.map(x => x.id === s.id ? { ...x, status: 'synced', synced: 'Just now' } : x)); toast('Drive re-synced'); }} /> : <IconButton icon="more-horizontal" label="More" size="sm" />}
          </div>
        ))}
      </Card>
      {dlg && <Dialog title="Connect Jira" description="Cortex reads issues and comments people already have access to. Nothing is shared beyond existing permissions." onClose={() => setDlg(false)}
        footer={<><Button variant="secondary" onClick={() => setDlg(false)}>Cancel</Button><Button variant="accent" icon="plug" onClick={() => { setDlg(false); toast('Jira connected · 2,318 issues indexing'); }}>Connect</Button></>}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}><Input label="Site URL" defaultValue="companya.atlassian.net" /><Checkbox label="Include archived projects" /><Checkbox label="Respect issue-level security" defaultChecked description="Recommended — Dana will thank you" /></div>
      </Dialog>}
    </div>
  );
}
