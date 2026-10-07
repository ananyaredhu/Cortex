function PeopleView({ toast }) {
  const { Card, Avatar, Badge, Select, Input, Button } = window.Cortex;
  const [q, setQ] = React.useState('');
  const shown = PEOPLE.filter(p => (p.name + p.role + p.email).toLowerCase().includes(q.toLowerCase()));
  const tone = { Admin: 'inverse', Member: 'neutral', Auditor: 'info', Guest: 'warning' };
  return (
    <div style={{ maxWidth: 960, margin: '0 auto', padding: '40px 32px', display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader eyebrow="Company A" title="People & access" action={<Button icon="user-plus">Invite</Button>} />
      <Input icon="search" placeholder="Search people…" value={q} onChange={e => setQ(e.target.value)} style={{ maxWidth: 320 }} />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(260px,1fr))', gap: 12 }}>
        {shown.map(p => (
          <Card key={p.id} padding={16}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                <Avatar src={p.src} name={p.name} size={56} shape="rounded" />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}><span style={{ font: '700 16px/1.2 var(--font-sans)' }}>{p.name}</span><Badge tone={tone[p.access]}>{p.access}</Badge></div>
                  <div style={{ font: '400 13px/1.4 var(--font-sans)', color: 'var(--text-body)' }}>{p.role} · {p.team}</div>
                  <div style={{ font: '500 12px/1.4 var(--font-mono)', color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.email}</div>
                </div>
              </div>
              <Select size="sm" defaultValue={p.access} options={['Admin', 'Member', 'Auditor', 'Guest']} onChange={e => toast('Access updated for ' + p.name)} />
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
