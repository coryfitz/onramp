# onramp

OnRamp is an early-stage full-stack Python framework for building apps that
run on the web, iOS, and Android with a shared React Native frontend.

## Installation

```bash
pip install onramp
```

## Create an app

Start a new OnRamp app:

```bash
onramp new <app_name>
```

The default is web-first: it creates the shared universal frontend without
creating iOS or Android projects. Native projects are added automatically when
you first run `onramp ios` or `onramp android`.

Create the app with both mobile projects immediately:

```
onramp new <app_name> --mobile
```

Create every currently supported frontend platform:

```
onramp new <app_name> --all
```

Just create an API (backend)
```
onramp new <app_name> --api
```

Run the development server

```
cd <app_name>
onramp run
```
If you have only created an API (there is no frontend build folder) then onramp run will only start the dev server for the backend.

If you created a fullstack app, then onramp run will start the dev server for the frontend app and will also start the dev server for the backend app if in your settings you have BACKEND = True.

Run a native app from the project directory:

```
onramp ios
onramp android
```

On macOS, `onramp ios` delegates the frontend launch to `onramp-js`. It adds the iOS project if it is missing, checks Xcode and CocoaPods, installs Pods, asks Xcode which simulators are actually compatible with the app, and selects one automatically. If the compatible iOS Simulator runtime is missing, OnRamp offers to download it before continuing. Xcode itself must still be installed separately; `onramp-js` uses Apple's installed toolchain.

`onramp android` delegates the frontend launch to `onramp-js`. It adds the Android project if it is missing, locates an installed Android SDK and virtual device, adds `adb` and the emulator to the command environment, selects JDK 17 for Gradle, and wakes emulators restored from an asleep Quick Boot snapshot automatically. These settings apply only to the frontend process, so no shell-profile editing is required.


The OnRamp App Framework Philosophy

Goal
Enable one person, with one Python codebase, to create an app that can run on any platform and scale from a startup to an enterprise

Design Considerations
Python on Everything
Python is a general-purpose programming language – therefore you should be able to use it to do anything a computer can do without needing to know another general-purpose programming language. There is no reason a Python developer should have to learn JavaScript to interact with web technologies – this is the perfect job for a compiler. OnRamp will allow programmers to create apps that run anywhere knowing only Python.

HTML and CSS are the Universal UI Primitives
With the advent of React Native, web technologies (HTML and CSS) are the universal primitives of UI design. Therefore, a Python web framework should not abstract away HTML and CSS.
As much as possible, syntax should be the same, no matter which platform you are writing for. One possibility would be to use React Native for Web, which uses native mobile primitives (such as <View>) and does create a unified syntax. However, this would separate OnRamp developers from web developers too much and make it difficult to use web tutorials. Therefore, React Strict DOM is a better choice for OnRamp. The only downside of this choice is that it uses a subset of DOM elements, and not all DOM elements, but the upside of still using HTML elements, plus the fact that Meta itself is putting more development effort into Strict DOM, make this the right choice for OnRamp.

Write Once, Run Anywhere
React Native allows us to use a popular web framework to create apps that will run on the web, as mobile apps, and even as TV or virtual reality apps. The Python ecosystem should take advantage of this technology.

Client-First
Client-first patterns provide the maximum amount of responsiveness, flexibility, and privacy necessary for the kinds of modern applications that the OnRamp project seeks to enable. Projects such as htmx have breathed new life into server-first patterns (which are similar to, but not the same as, hypermedia-driven apps), and naturally languages like Python, which due to the nature of the web and mobile platforms are more at home on the server than the client, work well with server-first frameworks. However, for the goal of OnRamp, a client-first approach is more appropriate (the only exception being if the user wants to only create an API). The challenge of getting Python into browsers and into phones is merely a technical one: Python can be compiled into JavaScript, React Native can handle the compilation into native code.

Don’t Hide Inherent Complexity
Creating server-client apps involves some inherent complexity. The programmer should never be unaware about whether their code will be running on the server or the client – that level of magic leads to confusion. Although the OnRamp programmer will not need to create two parallel apps themself, the server-client divide will be clear to the experience OnRamp programmer.

Gradual Typing
OnRamp should take full advantage of Python’s type hints, but the user should not need to use types to create a fully functional OnRamp app.

Async by Default
Modern applications are async by default and so is OnRamp. OnRamp uses Starlette as the backend API webserver and Tortoise as the ORM, both of which are async libraries.

Batteries-Included
OnRamp should include everything that you need to build a universal app, including auth. OnRamp apps should be easy to customize, but there should be an obvious OnRamp way of doing things, so that the beginning programmer can focus on the business logic of their app, not architectural decisions.

New Programmers are First-Class Citizens
The developer experience of new OnRamp programmers takes precedence over power users.

Specific Design Considerations

The long term goal is for the onramp-js React Native frontend to be written in Python and then transpiled into React Native code. This is why that code goes in a 'build' directory - eventually that code will be built by a transpiler and shouldn't be edited by hand. In this initial phase, however, we will edit the build directory and treat it as the frontend directory. The frontend code will be defined in Python in the app/components directory.

Frontend Generator

The React Native frontend generator lives in the `onramp-js` directory and is published as the `onramp-js` npm package. The Python `onramp new` command invokes the compatible pinned version of that package to create the completed React Native app in the project's `build` directory.

When developing OnRamp from this repository, the Python CLI automatically uses the local `onramp-js` source. An installed OnRamp Python package uses the `onramp-js` version specified in `src/onramp/config.toml`.

The generator can also be invoked directly. This creates a web-ready app in
`myapp/`:

```
npx onramp-js create myapp
```

Native projects can be added later:

```
cd myapp
npx onramp-js add ios
npx onramp-js add android
npx onramp-js add mobile
```

The standalone package also checks and runs each platform:

```
npx onramp-js doctor ios
npx onramp-js doctor android
npx onramp-js run web
npx onramp-js run ios
npx onramp-js run android
```
