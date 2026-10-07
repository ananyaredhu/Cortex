Modal for focused tasks (connect a source, confirm a destructive action).
```jsx
<Dialog title="Connect Jira" description="Cortex will read issues you can see." onClose={close}
  footer={<><Button variant="secondary" onClick={close}>Cancel</Button><Button variant="accent">Connect</Button></>} />
```