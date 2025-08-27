# onramp

Start a new OnRamp app

```
onramp new <app_name>
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