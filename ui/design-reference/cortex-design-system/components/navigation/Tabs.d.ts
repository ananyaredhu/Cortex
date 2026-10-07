export interface TabsProps {
  tabs: (string | { value: string; label: string; count?: number })[];
  value?: string;
  defaultValue?: string;
  onChange?: (value: string) => void;
  /** underline = page sections (orange indicator); segmented = view toggles */
  variant?: 'underline' | 'segmented';
}
export declare function Tabs(props: TabsProps): JSX.Element;