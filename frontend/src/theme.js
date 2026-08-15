import { createTheme } from '@mui/material/styles';
import { darkTokens } from './theme-tokens.js';

export const theme = createTheme({
  palette: {
    mode: 'dark',
    primary: {
      main: darkTokens.semantic.brand,
      light: darkTokens.accent.blue,
      dark: darkTokens.semantic.brandActive,
      contrastText: darkTokens.text.inverse,
    },
    secondary: {
      main: darkTokens.semantic.discovery,
      light: darkTokens.accent.purple,
      dark: darkTokens.semantic.discoveryActive,
      contrastText: darkTokens.text.inverse,
    },
    background: {
      default: darkTokens.surface.sunken,
      paper: darkTokens.surface.default,
    },
    text: {
      primary: darkTokens.text.default,
      secondary: darkTokens.text.subtle,
      disabled: darkTokens.text.disabled,
    },
    divider: darkTokens.border.default,
    info: { main: darkTokens.semantic.information },
    success: { main: darkTokens.semantic.success },
    warning: { main: darkTokens.semantic.warning },
    error: { main: darkTokens.semantic.danger },
    action: {
      active: darkTokens.text.subtle,
      hover: darkTokens.background.neutral,
      hoverOpacity: 1,
      selected: darkTokens.background.selected,
      selectedOpacity: 1,
      disabled: darkTokens.text.disabled,
      disabledBackground: darkTokens.background.disabled,
      disabledOpacity: 1,
      focus: darkTokens.background.selected,
      focusOpacity: 1,
    },
    controlGraph: darkTokens,
  },
  shape: { borderRadius: 10 },
  typography: {
    fontFamily: 'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    h5: { fontWeight: 700, letterSpacing: '-0.02em' },
    h6: { fontWeight: 700, letterSpacing: '-0.01em' },
    subtitle2: { fontWeight: 700 },
    button: { textTransform: 'none', fontWeight: 650 },
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        ':root': {
          colorScheme: 'dark',
          '--controlgraph-focus-ring': darkTokens.border.focused,
        },
        body: { backgroundColor: darkTokens.surface.sunken },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: ({ ownerState }) => ({
          backgroundImage: 'none',
          ...(ownerState.elevation > 0 && { backgroundColor: darkTokens.surface.raised }),
          ...(ownerState.elevation >= 8 && { backgroundColor: darkTokens.surface.overlay }),
        }),
      },
    },
    MuiAppBar: {
      styleOverrides: {
        root: {
          backgroundImage: 'none',
          backgroundColor: darkTokens.surface.raised,
        },
      },
    },
    MuiButton: {
      styleOverrides: {
        outlinedInherit: {
          borderColor: darkTokens.border.bold,
          '&:hover': {
            borderColor: darkTokens.border.focused,
            backgroundColor: darkTokens.background.neutral,
          },
        },
        containedPrimary: {
          color: darkTokens.text.inverse,
          backgroundColor: darkTokens.semantic.brand,
          '&:hover': { backgroundColor: darkTokens.semantic.brandHovered },
          '&:active': { backgroundColor: darkTokens.semantic.brandPressed },
        },
      },
    },
    MuiIconButton: {
      styleOverrides: {
        root: {
          '&:hover': { backgroundColor: darkTokens.background.neutralHovered },
        },
      },
    },
    MuiOutlinedInput: {
      styleOverrides: {
        root: {
          backgroundColor: darkTokens.background.input,
          '&:hover': { backgroundColor: darkTokens.background.inputHovered },
          '& .MuiOutlinedInput-notchedOutline': { borderColor: darkTokens.border.input },
          '&:hover .MuiOutlinedInput-notchedOutline': { borderColor: darkTokens.border.bold },
          '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
            borderColor: darkTokens.border.focused,
            borderWidth: 2,
          },
        },
      },
    },
    MuiTableCell: {
      styleOverrides: {
        root: { borderColor: darkTokens.border.default },
        head: { backgroundColor: darkTokens.surface.raised, fontWeight: 700 },
      },
    },
    MuiTableRow: {
      styleOverrides: {
        root: {
          '&.MuiTableRow-hover:hover': { backgroundColor: darkTokens.background.neutral },
          '&.Mui-selected, &.Mui-selected:hover': { backgroundColor: darkTokens.background.selected },
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: { fontWeight: 650 },
        filled: { backgroundColor: darkTokens.background.neutral },
        outlinedSuccess: {
          color: darkTokens.semantic.success,
          borderColor: darkTokens.semantic.success,
          backgroundColor: darkTokens.background.success,
        },
        outlinedWarning: {
          color: darkTokens.semantic.warning,
          borderColor: darkTokens.semantic.warning,
          backgroundColor: darkTokens.background.warning,
        },
      },
    },
    MuiCheckbox: {
      styleOverrides: {
        root: {
          color: darkTokens.text.subtlest,
          '&.Mui-checked': { color: darkTokens.semantic.brand },
        },
      },
    },
    MuiAlert: {
      styleOverrides: {
        standardInfo: { backgroundColor: darkTokens.background.information },
        standardSuccess: { backgroundColor: darkTokens.background.success },
        standardWarning: { backgroundColor: darkTokens.background.warning },
        standardError: { backgroundColor: darkTokens.background.danger },
        outlinedInfo: { backgroundColor: darkTokens.background.information },
      },
    },
    MuiDialog: {
      styleOverrides: {
        paper: {
          backgroundColor: darkTokens.surface.overlay,
          boxShadow: darkTokens.shadow.overlay,
        },
      },
    },
    MuiTooltip: {
      styleOverrides: {
        tooltip: {
          color: darkTokens.text.default,
          backgroundColor: darkTokens.surface.overlay,
          border: `1px solid ${darkTokens.border.default}`,
        },
      },
    },
    MuiSnackbarContent: {
      styleOverrides: {
        root: {
          color: darkTokens.text.default,
          backgroundColor: darkTokens.surface.overlay,
          border: `1px solid ${darkTokens.border.default}`,
          boxShadow: darkTokens.shadow.overlay,
        },
      },
    },
    MuiButtonBase: { defaultProps: { disableRipple: true } },
  },
});
