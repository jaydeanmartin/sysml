/* Debian builds XFOIL with floating-point traps on (-ffpe-trap), so a NaN in a
   boundary-layer iteration that XFOIL would recover from kills the process.
   gfortran turns the traps on by calling _gfortran_set_fpe at startup: preloaded
   into XFOIL, this makes that call a no-op. */
void _gfortran_set_fpe(int traps) { (void)traps; }
