/** Add buttons here; motion names match groups in kurisu.model3.json. */
export const interactions = {
  special: 
  {
    backendId: 1,
    motion: "TapReaction",
    label: "Special touch",
    // All four values use the shared 600 x 800 character design space.
    // 15% x 7.5% corresponds to 90 x 60 design pixels.
    position: { top: "45%", left: "50%", width: "30%", height: "10.5%" },
  },

  head: 
  {
    backendId: 2,
    motion: "PatReaction",
    label: "Head Pat",
    // All four values use the shared 600 x 800 character design space.
    // 15% x 7.5% corresponds to 90 x 60 design pixels.
    position: { top: "11%", left: "50%", width: "25%", height: "7.5%" },
  },
};

export type InteractionName = keyof typeof interactions;

/** A tap on her shoulders pokes her: she starts talking (the same as the
 *  Poke button by the message box). Same 600 x 800 design space. */
export const pokeSpots = {
  leftShoulder: {
    label: "Poke her shoulder",
    position: { top: "33.5%", left: "39%", width: "11%", height: "6%" },
  },
  rightShoulder: {
    label: "Poke her shoulder",
    position: { top: "33.5%", left: "61%", width: "11%", height: "6%" },
  },
};
