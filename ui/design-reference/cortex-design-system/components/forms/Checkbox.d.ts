export interface CheckboxProps {
  label?: string;
  description?: string;
  checked?: boolean;
  defaultChecked?: boolean;
  onChange?: (checked: boolean) => void;
  disabled?: boolean;
}
export declare function Checkbox(props: CheckboxProps): JSX.Element;