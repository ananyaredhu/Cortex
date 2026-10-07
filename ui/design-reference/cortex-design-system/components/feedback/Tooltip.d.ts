export interface TooltipProps {
  label: string;
  children: React.ReactNode;
  side?: 'top' | 'bottom';
  /** Force visible (docs) */
  open?: boolean;
}
export declare function Tooltip(props: TooltipProps): JSX.Element;