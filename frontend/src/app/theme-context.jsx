/** @jsx React.createElement */
const { useContext } = React;

const ThemeContext = React.createContext({
  theme: 'dark',
  isDark: true,
  toggleTheme: () => {},
  setTheme: () => {},
});

function useTheme() {
  return useContext(ThemeContext);
}

window.FTTH_APP = window.FTTH_APP || {};
window.FTTH_APP.ThemeContext = ThemeContext;
window.FTTH_APP.useTheme = useTheme;

