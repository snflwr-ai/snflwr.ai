import"./CWj6FrbW.js";import"./CN51-NxK.js";import{b as Q,o as U,i as V,d as W,e as X,a as v,p as Y,j as Z,m as $,t as M,x as n,v as y,r as ee,f as z}from"./Cq_rWCdd.js";import{i as te}from"./DlYoNXTi.js";import{e as ae,i as ne}from"./CIY3QYOg.js";import{e as ie,s as oe}from"./7egFszuo.js";import{i as re}from"./8kT0SYrj.js";import{p as t}from"./DwH1BKTo.js";const he=l=>{typeof document>"u"||document.documentElement.style.setProperty("--app-text-scale",`${l}`)};var le=z('<div class="confetti svelte-rtt661"></div>'),se=z("<div></div>");function _e(l,e){Q(e,!1);let b=t(e,"size",8,10),s=t(e,"x",24,()=>[-.5,.5]),d=t(e,"y",24,()=>[.25,1]),m=t(e,"duration",8,2e3),c=t(e,"infinite",8,!1),r=t(e,"delay",24,()=>[0,50]),x=t(e,"colorRange",24,()=>[0,360]),f=t(e,"colorArray",24,()=>[]),k=t(e,"amount",8,50),u=t(e,"iterationCount",8,1),S=t(e,"fallDistance",8,"100px"),p=t(e,"rounded",8,!1),w=t(e,"cone",8,!1),A=t(e,"noGravity",8,!1),D=t(e,"xSpread",8,.15),G=t(e,"destroyOnComplete",8,!0),g=$(!1);U(()=>{!G()||c()||u()=="infinite"||setTimeout(()=>V(g,!0),(m()+r()[1])*u())});function a(i,o){return Math.random()*(o-i)+i}function O(){return f().length?f()[Math.round(Math.random()*(f().length-1))]:`hsl(${Math.round(a(x()[0],x()[1]))}, 75%, 50%)`}re();var h=W(),R=X(h);{var T=i=>{var o=se();let _;ae(o,5,()=>({length:k()}),ne,(j,de)=>{var C=le();M((B,E,P,q,F,H,I,J,K,L,N)=>ie(C,`
        --fall-distance: ${S()??""};
        --size: ${b()??""}px;
        --color: ${B??""};
        --skew: ${E??""}deg,${P??""}deg;
        --rotation-xyz: ${q??""}, ${F??""}, ${H??""};
        --rotation-deg: ${I??""}deg;
        --translate-y-multiplier: ${J??""};
        --translate-x-multiplier: ${K??""};
        --scale: ${L??""};
        --transition-duration: ${c()?`calc(${m()}ms * var(--scale))`:`${m()}ms`};
        --transition-delay: ${N??""}ms;
        --transition-iteration-count: ${(c()?"infinite":u())??""};
        --x-spread: ${1-D()}`),[()=>n(O),()=>n(()=>a(-45,45)),()=>n(()=>a(-45,45)),()=>n(()=>a(-10,10)),()=>n(()=>a(-10,10)),()=>n(()=>a(-10,10)),()=>n(()=>a(0,360)),()=>(y(d()),n(()=>a(d()[0],d()[1]))),()=>(y(s()),n(()=>a(s()[0],s()[1]))),()=>n(()=>.1*a(2,10)),()=>(y(r()),n(()=>a(r()[0],r()[1])))]),v(j,C)}),ee(o),M(()=>_=oe(o,1,"confetti-holder svelte-rtt661",null,_,{rounded:p(),cone:w(),"no-gravity":A()})),v(i,o)};te(R,i=>{Z(g)||i(T)})}v(l,h),Y()}export{_e as C,he as s};
//# sourceMappingURL=BDJSG-og.js.map
