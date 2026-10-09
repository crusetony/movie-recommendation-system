import { alpha, createTheme } from '@mui/material/styles';

// Shared colors and component styles for recommendation and annotation pages.
const theme = createTheme({
  palette: {
    mode: 'dark',
    primary: { main: '#dfff00', light: '#edff80', dark: '#bdd600', contrastText: '#0a0e27' },
    secondary: { main: '#91adff', light: '#b7c9ff', dark: '#2454ff', contrastText: '#0a0e27' },
    success: { main: '#8bdbb5', contrastText: '#0a0e27' },
    info: { main: '#91b4ff', contrastText: '#0a0e27' },
    warning: { main: '#f4ca78', contrastText: '#0a0e27' },
    error: { main: '#ff9f98', contrastText: '#0a0e27' },
    background: { default: '#080d19', paper: '#111c30' },
    text: { primary: '#e8eaf0', secondary: '#b2bed7', disabled: '#8090b0' },
    divider: 'rgba(255,255,255,0.1)',
    grey: {
      50: '#10162e', 100: '#19213d', 200: '#26314f', 300: '#455574',
      400: '#647796', 500: '#8090b0', 600: '#a0aec8', 700: '#b2bed7',
      800: '#d0d8e8', 900: '#e8eaf0',
    },
  },
  shape: { borderRadius: 12 },
  typography: {
    fontFamily: 'var(--cinema-font-family)',
    h4: { fontWeight: 700, letterSpacing: '-0.035em', lineHeight: 1.35 },
    h5: { fontWeight: 650, fontSize: '1.4rem', letterSpacing: '-0.025em', lineHeight: 1.5 },
    h6: { fontWeight: 650, lineHeight: 1.5 },
    subtitle1: { fontWeight: 600 },
    subtitle2: { fontWeight: 600 },
    body1: { lineHeight: 1.75 },
    body2: { lineHeight: 1.75 },
    caption: { lineHeight: 1.65 },
    button: { fontWeight: 600, textTransform: 'none', letterSpacing: '0.01em' },
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: {
          colorScheme: 'dark',
          backgroundImage: 'radial-gradient(ellipse at 85% 0%, rgba(36,84,255,.13), transparent 50%)',
          backgroundAttachment: 'fixed',
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          backgroundImage: 'none', backgroundColor: 'rgba(17,28,48,.88)',
        },
        outlined: { borderColor: 'rgba(255,255,255,.1)' },
        elevation1: { border: '1px solid rgba(255,255,255,.1)', boxShadow: '0 8px 28px rgba(0,0,0,.2)' },
        elevation2: { border: '1px solid rgba(255,255,255,.1)', boxShadow: '0 12px 36px rgba(0,0,0,.25)' },
      },
    },
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: {
        root: {
          borderRadius: 8, padding: '9px 18px',
          transition: 'transform 260ms var(--motion-spring), background-color 180ms, border-color 180ms, box-shadow 180ms',
          '&:active:not(.Mui-disabled)': { transform: 'scale(.97)', transitionDuration: '70ms' },
        },
        sizeSmall: { padding: '5px 12px' },
        outlined: ({ theme, ownerState }) => ({
          borderColor: alpha(theme.palette[ownerState.color === 'inherit' ? 'primary' : ownerState.color || 'primary'].main, 0.4),
          backgroundColor: 'rgba(255,255,255,.02)',
        }),
      },
    },
    MuiOutlinedInput: {
      styleOverrides: {
        root: ({ theme }) => ({
          borderRadius: 10,
          backgroundColor: 'rgba(0,0,0,.2)',
          '& .MuiOutlinedInput-notchedOutline': { borderColor: 'rgba(255,255,255,.18)' },
          '&:hover .MuiOutlinedInput-notchedOutline': { borderColor: '#8094bf' },
          '&.Mui-focused': { boxShadow: `0 0 0 3px ${alpha(theme.palette.primary.main, 0.1)}` },
        }),
      },
    },
    MuiInputLabel: { styleOverrides: { root: { color: '#b2bed7' } } },
    MuiFormHelperText: { styleOverrides: { root: { marginTop: 7 } } },
    MuiChip: {
      styleOverrides: {
        root: ({ theme, ownerState }) => {
          const color = ownerState.color && ownerState.color !== 'default'
            ? theme.palette[ownerState.color].main : theme.palette.text.secondary;
          return {
            borderRadius: 6,
            fontWeight: 500,
            ...(ownerState.variant !== 'outlined' ? { color, backgroundColor: alpha(color, 0.12) } : { borderColor: alpha(color, 0.35) }),
            '& .MuiChip-icon': { color: 'inherit' },
          };
        },
      },
    },
    MuiTabs: {
      styleOverrides: {
        root: { minHeight: 46, padding: 4, borderRadius: 10, backgroundColor: 'rgba(255,255,255,.04)', border: '1px solid rgba(255,255,255,.1)', width: 'fit-content', maxWidth: '100%' },
        indicator: {
          top: 0, height: '100%', borderRadius: 7,
          backgroundColor: 'rgba(223,255,0,.12)', border: '1px solid rgba(223,255,0,.2)',
          transition: 'left 360ms var(--motion-spring), width 360ms var(--motion-spring)',
        },
      },
    },
    MuiTab: {
      styleOverrides: {
        root: { minHeight: 38, padding: '8px 24px', borderRadius: 7, zIndex: 1, transition: 'color 180ms, background-color 180ms' },
      },
    },
    MuiAlert: {
      styleOverrides: {
        root: { borderRadius: 12, alignItems: 'center' },
        standard: ({ theme, ownerState }) => {
          const color = theme.palette[ownerState.severity || 'success'].main;
          return { color, backgroundColor: alpha(color, 0.09), '& .MuiAlert-icon': { color } };
        },
      },
    },
    MuiCard: {
      styleOverrides: { root: { borderRadius: 14, boxShadow: '0 4px 20px rgba(0,0,0,.2)' } },
    },
    MuiAccordion: {
      styleOverrides: {
        root: { backgroundColor: 'rgba(0,0,0,.15)', backdropFilter: 'none', '&:before': { display: 'none' } },
      },
    },
    MuiToggleButton: {
      styleOverrides: {
        root: ({ theme }) => ({
          borderColor: theme.palette.divider,
          transition: 'transform 240ms var(--motion-spring), background-color 180ms, color 180ms',
          '&:active:not(.Mui-disabled)': { transform: 'scale(.96)', transitionDuration: '70ms' },
          '&.Mui-selected': { color: theme.palette.primary.main, backgroundColor: alpha(theme.palette.primary.main, .12) },
          '&.Mui-selected:hover': { backgroundColor: alpha(theme.palette.primary.main, .2) },
        }),
      },
    },
    MuiIconButton: {
      styleOverrides: { root: {
        transition: 'transform 240ms var(--motion-spring), background-color 180ms',
        '&:active:not(.Mui-disabled)': { transform: 'scale(.9)', transitionDuration: '70ms' },
      } },
    },
    MuiTableCell: {
      styleOverrides: { head: { backgroundColor: 'rgba(36,84,255,.12)', fontWeight: 600 }, root: { borderColor: 'rgba(255,255,255,.1)' } },
    },
    MuiLinearProgress: { styleOverrides: { root: { backgroundColor: 'rgba(255,255,255,.08)' } } },
    MuiDialog: { styleOverrides: { paper: { borderRadius: 16, backgroundColor: 'rgba(20,26,58,.96)' } } },
    MuiMenu: { styleOverrides: { paper: { backgroundColor: 'rgba(20,26,58,.97)' } } },
    MuiAutocomplete: { styleOverrides: { paper: { backgroundColor: 'rgba(20,26,58,.97)' } } },
  },
});

export default theme;
