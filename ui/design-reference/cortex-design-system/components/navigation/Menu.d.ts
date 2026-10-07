export interface MenuItem { label: string; icon?: string; avatar?: string; shortcut?: string; checked?: boolean; danger?: boolean; }
export interface MenuProps {
  sections: { title?: string; items: MenuItem[] }[];
  onSelect?: (item: MenuItem) => void;
  width?: number;
  style?: React.CSSProperties;
}
export declare function Menu(props: MenuProps): JSX.Element;