import { darkTokens } from './theme-tokens.js';

export const KIND_LABELS = {
  SEL_DEVICE: 'SEL device',
  PROTOCOL_POINT: 'Protocol point',
  RTAC_TAG: 'RTAC tag',
  IEC_VARIABLE: 'IEC variable',
  IEC_LOGIC: 'IEC logic',
  IGNITION_DEVICE: 'Connection device',
  OPC_ITEM: 'OPC item',
  UDT_DEFINITION: 'UDT definition',
  UDT_INSTANCE: 'UDT instance',
  UDT_MEMBER: 'UDT member',
  IGNITION_TAG: 'Ignition tag',
  MAPPING_ISSUE: 'Mapping issue',
};

export const SYSTEM_COLORS = {
  SEL: darkTokens.accent.blue,
  IGNITION: darkTokens.accent.teal,
  RESOLVER: darkTokens.accent.orange,
};

export const SYSTEM_FILTERS = [
  { label: 'SEL RTAC', value: 'SEL' },
  { label: 'Ignition', value: 'IGNITION' },
  { label: 'Resolver issues', value: 'RESOLVER' },
];

export const TYPE_FILTERS = [
  { label: 'Device', kinds: ['SEL_DEVICE', 'IGNITION_DEVICE'] },
  { label: 'Protocol point', kinds: ['PROTOCOL_POINT', 'OPC_ITEM'] },
  { label: 'Tag', kinds: ['RTAC_TAG', 'IGNITION_TAG'] },
  { label: 'Logic', kinds: ['IEC_VARIABLE', 'IEC_LOGIC'] },
  { label: 'UDT', kinds: ['UDT_DEFINITION', 'UDT_INSTANCE', 'UDT_MEMBER'] },
  { label: 'Issue', kinds: ['MAPPING_ISSUE'] },
];

export const FLOW_NODE_WIDTH = 184;
export const FLOW_NODE_HEIGHT = 76;
