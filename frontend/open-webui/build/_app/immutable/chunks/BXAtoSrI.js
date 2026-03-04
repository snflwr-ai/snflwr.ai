import"./CWj6FrbW.js";import"./CN51-NxK.js";import{b as Oe,g as Ke,u as he,v as D,w as ze,e as Je,j as d,i as v,k as u,c as r,r as a,l as O,a as h,t as w,x as l,s as c,p as Qe,f as g,m as C,y as K,n as k}from"./Cq_rWCdd.js";import{i as Ve}from"./DlYoNXTi.js";import{r as z,a as J}from"./7egFszuo.js";import{b as Q}from"./Bk7P8jfj.js";import{b as ge}from"./BMifpPng.js";import{p as Xe}from"./Bfc47y5P.js";import{i as Ze}from"./8kT0SYrj.js";import{p as _}from"./DwH1BKTo.js";import{a as xe,s as et}from"./DYhThC2r.js";import{t as ye}from"./D9TDmrZ0.js";import{g as tt}from"./h2Rvv4mb.js";import{u as rt}from"./CT_a_LVJ.js";import{u as at}from"./DBq10G1h.js";import{C as st}from"./DtIoLMyt.js";import{C as ot}from"./DNIH2u3-.js";import{C as it}from"./ByylcQmC.js";import{T as G}from"./C_lVvbXk.js";import{A as lt,L as nt}from"./CROAgdSy.js";var dt=g('<button class="w-full text-left text-sm py-1.5 px-1 rounded-lg dark:text-gray-300 dark:hover:text-white hover:bg-black/5 dark:hover:bg-gray-850" type="button"><!></button>'),ut=g('<input class="w-full text-2xl bg-transparent outline-hidden" type="text" required/>'),ct=g('<div class="text-sm text-gray-500 shrink-0"> </div>'),mt=g('<input class="w-full text-sm disabled:text-gray-500 bg-transparent outline-hidden" type="text" required/>'),ft=g('<input class="w-full text-sm bg-transparent outline-hidden" type="text" required/>'),vt=g('<div class="text-sm text-gray-500"><div class=" bg-yellow-500/20 text-yellow-700 dark:text-yellow-200 rounded-lg px-4 py-3"><div> </div> <ul class=" mt-1 list-disc pl-4 text-xs"><li> </li> <li> </li></ul></div> <div class="my-3"> </div></div>'),_t=g('<!> <div class=" flex flex-col justify-between w-full overflow-y-auto h-full"><div class="mx-auto w-full md:px-0 h-full"><form class=" flex flex-col max-h-[100dvh] h-full"><div class="flex flex-col flex-1 overflow-auto h-0 rounded-lg"><div class="w-full mb-2 flex flex-col gap-0.5"><div class="flex w-full items-center"><div class=" shrink-0 mr-2"><!></div> <div class="flex-1"><!></div> <div class="self-center shrink-0"><button class="bg-gray-50 hover:bg-gray-100 text-black dark:bg-gray-850 dark:hover:bg-gray-800 dark:text-white transition px-2 py-1 rounded-full flex gap-1 items-center" type="button"><!> <div class="text-sm font-medium shrink-0"> </div></button></div></div> <div class=" flex gap-2 px-1 items-center"><!> <!></div></div> <div class="mb-2 flex-1 overflow-auto h-0 rounded-lg"><!></div> <div class="pb-3 flex justify-between"><div class="flex-1 pr-3"><div class="text-xs text-gray-500 line-clamp-2"><span class=" font-semibold dark:text-gray-200"> </span> <br/>— <span class=" font-medium dark:text-gray-400"> </span></div></div> <button class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full" type="submit"> </button></div></div></form></div></div> <!>',1);function Mt(be,m){Oe(m,!1);const x=()=>xe(rt,"$user",V),e=()=>xe(ke,"$i18n",V),[V,we]=et(),ke=Ke("i18n");let T=C(null),N=C(!1),S=C(!1),y=_(m,"edit",8,!1),X=_(m,"clone",8,!1),$e=_(m,"onSave",8,()=>{}),b=_(m,"id",12,""),$=_(m,"name",12,""),E=_(m,"meta",28,()=>({description:""})),p=_(m,"content",12,""),q=_(m,"accessGrants",28,()=>[]),P=C("");const Ce=()=>{v(P,p())};let A=C(),Te=`import os
import requests
from datetime import datetime
from pydantic import BaseModel, Field

class Tools:
    def __init__(self):
        pass

    # Add your custom tools using pure Python code here, make sure to add type hints and descriptions
	
    def get_user_name_and_email_and_id(self, __user__: dict = {}) -> str:
        """
        Get the user name, Email and ID from the user object.
        """

        # Do not include a descrption for __user__ as it should not be shown in the tool's specification
        # The session user object will be passed as a parameter when the function is called

        print(__user__)
        result = ""

        if "name" in __user__:
            result += f"User: {__user__['name']}"
        if "id" in __user__:
            result += f" (ID: {__user__['id']})"
        if "email" in __user__:
            result += f" (Email: {__user__['email']})"

        if result == "":
            result = "User: Unknown"

        return result

    def get_current_time(self) -> str:
        """
        Get the current time in a more human-readable format.
        """

        now = datetime.now()
        current_time = now.strftime("%I:%M:%S %p")  # Using 12-hour format with AM/PM
        current_date = now.strftime(
            "%A, %B %d, %Y"
        )  # Full weekday, month name, day, and year

        return f"Current Date and Time = {current_date}, {current_time}"

    def calculator(
        self,
        equation: str = Field(
            ..., description="The mathematical equation to calculate."
        ),
    ) -> str:
        """
        Calculate the result of an equation.
        """

        # Avoid using eval in production code
        # https://nedbatchelder.com/blog/201206/eval_really_is_dangerous.html
        try:
            result = eval(equation)
            return f"{equation} = {result}"
        except Exception as e:
            print(e)
            return "Invalid equation"

    def get_current_weather(
        self,
        city: str = Field(
            "New York, NY", description="Get the current weather for a given city."
        ),
    ) -> str:
        """
        Get the current weather for a given city.
        """

        api_key = os.getenv("OPENWEATHER_API_KEY")
        if not api_key:
            return (
                "API key is not set in the environment variable 'OPENWEATHER_API_KEY'."
            )

        base_url = "http://api.openweathermap.org/data/2.5/weather"
        params = {
            "q": city,
            "appid": api_key,
            "units": "metric",  # Optional: Use 'imperial' for Fahrenheit
        }

        try:
            response = requests.get(base_url, params=params)
            response.raise_for_status()  # Raise HTTPError for bad responses (4xx and 5xx)
            data = response.json()

            if data.get("cod") != 200:
                return f"Error fetching weather data: {data.get('message')}"

            weather_description = data["weather"][0]["description"]
            temperature = data["main"]["temp"]
            humidity = data["main"]["humidity"]
            wind_speed = data["wind"]["speed"]

            return f"Weather in {city}: {temperature}°C"
        except requests.RequestException as e:
            return f"Error fetching weather data: {str(e)}"
`;const Ee=async()=>{$e()({id:b(),name:$(),meta:E(),content:p(),access_grants:q()})},Z=async()=>{if(d(A)){p(d(P)),await K();const t=await d(A).formatPythonCodeHandler();await K(),p(d(P)),await K(),t&&Ee()}};he(()=>D(p()),()=>{p()&&Ce()}),he(()=>(D($()),D(y()),D(X())),()=>{$()&&!y()&&!X()&&b($().replace(/\s+/g,"_").toLowerCase())}),ze(),Ze();var ee=_t(),te=Je(ee);{let t=k(()=>(x(),l(()=>{var i,s,o,f;return((o=(s=(i=x())==null?void 0:i.permissions)==null?void 0:s.sharing)==null?void 0:o.tools)||((f=x())==null?void 0:f.role)==="admin"}))),n=k(()=>(x(),l(()=>{var i,s,o,f;return((o=(s=(i=x())==null?void 0:i.permissions)==null?void 0:s.sharing)==null?void 0:o.public_tools)||((f=x())==null?void 0:f.role)==="admin"})));lt(te,{accessRoles:["read","write"],get share(){return d(t)},get sharePublic(){return d(n)},onChange:async()=>{if(y()&&b())try{await at(localStorage.token,b(),q()),ye.success(e().t("Saved"))}catch(i){ye.error(`${i}`)}},get show(){return d(S)},set show(i){v(S,i)},get accessGrants(){return q()},set accessGrants(i){q(i)},$$legacy:!0})}var M=u(te,2),re=r(M),I=r(re),ae=r(I),j=r(ae),H=r(j),W=r(H),qe=r(W);{let t=k(()=>(e(),l(()=>e().t("Back"))));G(qe,{get content(){return d(t)},children:(n,i)=>{var s=dt(),o=r(s);it(o,{strokeWidth:"2.5"}),a(s),O("click",s,()=>{tt("/workspace/tools")}),h(n,s)},$$slots:{default:!0}})}a(W);var F=u(W,2),Pe=r(F);{let t=k(()=>(e(),l(()=>e().t("e.g. My Tools"))));G(Pe,{get content(){return d(t)},placement:"top-start",children:(n,i)=>{var s=ut();z(s),w(o=>J(s,"placeholder",o),[()=>(e(),l(()=>e().t("Tool Name")))]),Q(s,$),h(n,s)},$$slots:{default:!0}})}a(F);var se=u(F,2),R=r(se),oe=r(R);nt(oe,{strokeWidth:"2.5",className:"size-3.5"});var ie=u(oe,2),Ae=r(ie,!0);a(ie),a(R),a(se),a(H);var le=u(H,2),ne=r(le);{var Ie=t=>{var n=ct(),i=r(n,!0);a(n),w(()=>c(i,b())),h(t,n)},De=t=>{{let n=k(()=>(e(),l(()=>e().t("e.g. my_tools"))));G(t,{className:"w-full",get content(){return d(n)},placement:"top-start",children:(i,s)=>{var o=mt();z(o),w(f=>{J(o,"placeholder",f),o.disabled=y()},[()=>(e(),l(()=>e().t("Tool ID")))]),Q(o,b),h(i,o)},$$slots:{default:!0}})}};Ve(ne,t=>{y()?t(Ie):t(De,!1)})}var Ge=u(ne,2);{let t=k(()=>(e(),l(()=>e().t("e.g. Tools for performing various operations"))));G(Ge,{className:"w-full self-center items-center flex",get content(){return d(t)},placement:"top-start",children:(n,i)=>{var s=ft();z(s),w(o=>J(s,"placeholder",o),[()=>(e(),l(()=>e().t("Tool Description")))]),Q(s,()=>E().description,o=>E(E().description=o,!0)),h(n,s)},$$slots:{default:!0}})}a(le),a(j);var U=u(j,2),Ne=r(U);ge(st(Ne,{get value(){return p()},lang:"python",boilerplate:Te,onChange:t=>{v(P,t)},onSave:async()=>{d(T)&&d(T).requestSubmit()},$$legacy:!0}),t=>v(A,t),()=>d(A)),a(U);var de=u(U,2),Y=r(de),ue=r(Y),L=r(ue),Se=r(L,!0);a(L);var ce=u(L),me=u(ce,3),Me=r(me,!0);a(me),a(ue),a(Y);var fe=u(Y,2),je=r(fe,!0);a(fe),a(de),a(ae),a(I),ge(I,t=>v(T,t),()=>d(T)),a(re),a(M);var He=u(M,2);ot(He,{get show(){return d(N)},set show(t){v(N,t)},$$events:{confirm:()=>{Z()}},children:(t,n)=>{var i=vt(),s=r(i),o=r(s),f=r(o,!0);a(o);var ve=u(o,2),B=r(ve),We=r(B,!0);a(B);var _e=u(B,2),Fe=r(_e,!0);a(_e),a(ve),a(s);var pe=u(s,2),Re=r(pe,!0);a(pe),a(i),w((Ue,Ye,Le,Be)=>{c(f,Ue),c(We,Ye),c(Fe,Le),c(Re,Be)},[()=>(e(),l(()=>e().t("Please carefully review the following warnings:"))),()=>(e(),l(()=>e().t("Tools have a function calling system that allows arbitrary code execution."))),()=>(e(),l(()=>e().t("Do not install tools from sources you do not fully trust."))),()=>(e(),l(()=>e().t("I acknowledge that I have read and I understand the implications of my action. I am aware of the risks associated with executing arbitrary code and I have verified the trustworthiness of the source.")))]),h(t,i)},$$slots:{default:!0},$$legacy:!0}),w((t,n,i,s,o)=>{c(Ae,t),c(Se,n),c(ce,` ${i??""} `),c(Me,s),c(je,o)},[()=>(e(),l(()=>e().t("Access"))),()=>(e(),l(()=>e().t("Warning:"))),()=>(e(),l(()=>e().t("Tools are a function calling system with arbitrary code execution"))),()=>(e(),l(()=>e().t("don't install random tools from sources you don't trust."))),()=>(e(),l(()=>e().t("Save")))]),O("click",R,()=>{v(S,!0)}),O("submit",I,Xe(()=>{y()?Z():v(N,!0)})),h(be,ee),Qe(),we()}export{Mt as T};
//# sourceMappingURL=BXAtoSrI.js.map
