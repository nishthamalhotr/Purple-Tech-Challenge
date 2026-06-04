import { Router, type IRouter } from "express";
import healthRouter from "./health";
import eventsRouter from "./events";
import storesRouter from "./stores";

const router: IRouter = Router();

router.use(healthRouter);
router.use("/events", eventsRouter);
router.use("/stores", storesRouter);

export default router;
