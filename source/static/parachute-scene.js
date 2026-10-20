/* Shared read-only scene geometry. Extracted from the game's draw() function.
   Pure layout only: no API calls, bets, timers or writes. */
window.YiParachuteLayout=function(sceneWidth,sceneHeight,planeWidth,jumpHeight,height,jumped){
  const runwayStart=Math.max(planeWidth/2+14,sceneWidth*.15),ground=82-planeWidth*10/470;
  const maxRise=Math.max(0,sceneHeight-ground-planeWidth*190/470-24);
  const jumpX=runwayStart+Math.min(sceneWidth*.33,jumpHeight*2.5),jumpBottom=ground+Math.min(maxRise,jumpHeight*2.3);
  const afterJump=jumped?Math.max(0,height-jumpHeight):0;
  return {ground,jumpX,jumpBottom,x:jumped?jumpX+afterJump*2.5:runwayStart+Math.min(sceneWidth*.33,height*2.5),bottom:jumped?jumpBottom+afterJump*2.3:ground+Math.min(maxRise,height*2.3),angle:-Math.min(18,height*1.8)};
};
