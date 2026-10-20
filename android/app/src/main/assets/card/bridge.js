'use strict';let renderer;window.showCard=function(data){if(renderer)renderer.destroy();renderer=new window.YCardRenderer(document.getElementById('card'),data)};
